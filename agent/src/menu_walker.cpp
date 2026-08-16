#include "menu_walker.h"

#include "input_synth.h"

#include <QAbstractItemView>
#include <QAction>
#include <QApplication>
#include <QComboBox>
#include <QElapsedTimer>
#include <QMenu>
#include <QMenuBar>
#include <QPointer>
#include <QTimer>

#include <functional>
#include <utility>

namespace liberaqt {

namespace {

//: How long a popup gets to appear after the click that opens it. Popups are effectively
//: immediate -- this is slack for platform fade animations, not a wait for work.
constexpr int StageTimeoutMs = 1500;
constexpr int PollMs = 16;

// Runs a list of steps from the event loop. A step either reports Wait -- poll again until the
// stage deadline -- or Next; failures are thrown as CommandError and become the rejection. The
// object owns itself and dies when the flow ends either way.
class ClickFlow : public QObject
{
public:
    enum class Step { Wait, Next };
    using Fn = std::function<Step()>;

    ClickFlow(QList<Fn> steps, Dispatcher::Resolver resolve, Dispatcher::Rejecter reject,
              std::function<void()> cleanup, QString stageTimeoutMessage)
        : m_steps(std::move(steps)), m_resolve(std::move(resolve)), m_reject(std::move(reject)),
          m_cleanup(std::move(cleanup)), m_timeoutMessage(std::move(stageTimeoutMessage))
    {
        m_deadline.start();
        QTimer::singleShot(0, this, [this] { tick(); });
    }

private:
    void tick()
    {
        try {
            while (m_index < m_steps.size()) {
                if (m_steps.at(m_index)() == Step::Wait) {
                    if (m_deadline.elapsed() > StageTimeoutMs) {
                        throw CommandError(ErrorCode::Timeout,
                                           m_timeoutMessage.arg(m_index));
                    }
                    QTimer::singleShot(PollMs, this, [this] { tick(); });
                    return;
                }
                ++m_index;
                m_deadline.restart();
                // A step that queued input needs the queue drained before the next one can see
                // its effect, so yield to the event loop between steps rather than running on.
                if (m_index < m_steps.size()) {
                    QTimer::singleShot(0, this, [this] { tick(); });
                    return;
                }
            }
            m_resolve(QVariant());
        } catch (const CommandError &err) {
            if (m_cleanup)
                m_cleanup();
            m_reject(err);
        } catch (const std::exception &err) {
            if (m_cleanup)
                m_cleanup();
            m_reject(CommandError(ErrorCode::Internal, QString::fromUtf8(err.what())));
        }
        deleteLater();
    }

    QList<Fn> m_steps;
    Dispatcher::Resolver m_resolve;
    Dispatcher::Rejecter m_reject;
    std::function<void()> m_cleanup;
    QString m_timeoutMessage;
    QElapsedTimer m_deadline;
    int m_index = 0;
};

QString actionLabel(const QAction *action)
{
    QString label = action->text();
    return label.remove(QLatin1Char('&'));
}

// The entry labelled `wanted` among `actions`, with the same failed-lookup diagnostics
// WidgetBackend::menuPath produces: what *was* there, and where.
QAction *findEntry(const QList<QAction *> &actions, const QString &wanted,
                   const QString &context)
{
    QStringList available;
    for (QAction *action : actions) {
        const QString label = actionLabel(action);
        if (label == wanted)
            return action;
        if (!label.isEmpty())
            available.append(label);
    }
    throw CommandError(ErrorCode::NotFound,
                       QStringLiteral("no menu entry '%1' under %2; there is: %3")
                           .arg(wanted, context, available.join(QStringLiteral(", "))));
}

// What the walk has learned so far. Shared between the stages, because each stage's target can
// only be resolved once the previous stage's click has opened its menu.
struct Walk
{
    QPointer<QAction> current;      // the entry the last stage clicked
    QList<QPointer<QMenu>> opened;  // every menu the walk opened, for cleanup on failure
    QVariantMap leaf;               // filled by the final stage, becomes the resolution
};

QPoint entryPoint(QMenu *menu, QAction *action);

// Records what is being activated, read just before the final click -- afterwards a checkable
// entry has already toggled.
void noteLeaf(const std::shared_ptr<Walk> &walk, QAction *leaf)
{
    walk->leaf.insert(QStringLiteral("text"), actionLabel(leaf));
    walk->leaf.insert(QStringLiteral("enabled"), leaf->isEnabled());
    walk->leaf.insert(QStringLiteral("checked"), leaf->isChecked());
    walk->leaf.insert(QStringLiteral("clicked"), true);
}

// One stage: find `wanted` inside the menu that clicking `walk->current` opened, and click it.
// Looking the entry up only after the menu is on screen is the point -- aboutToShow has run.
ClickFlow::Fn levelStage(const std::shared_ptr<Walk> &walk, const QString &wanted,
                         const QString &context, bool lastStage)
{
    return [walk, wanted, context, lastStage]() {
        if (!walk->current)
            throw CommandError(ErrorCode::Stale, QStringLiteral("a menu entry disappeared"));
        QMenu *menu = walk->current->menu();
        if (!menu) {
            throw CommandError(ErrorCode::Unsupported,
                               QStringLiteral("'%1' is not a submenu").arg(context));
        }
        if (!menu->isVisible())
            return ClickFlow::Step::Wait;
        walk->opened.append(menu);
        QAction *entry = findEntry(menu->actions(), wanted, context);
        if (!entry->isEnabled()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("the menu entry '%1' is disabled").arg(wanted));
        }
        if (lastStage)
            noteLeaf(walk, entry);
        walk->current = entry;
        InputSynth::clickNative(menu, entryPoint(menu, entry));
        return ClickFlow::Step::Next;
    };
}

// Closes whatever a failed walk managed to open: a test that could not walk a menu should not
// leave one hanging over every later action, blocking input to everything behind it.
//
// Drains the popup *stack* rather than hiding the menus the walk recorded. An application is
// free to show its context menu with QMenu::exec(), and hiding such a menu from inside its own
// nested event loop does not reliably dismiss it -- Assistant leaves it on screen. close() ends
// the loop, and looping picks up submenus the walk never got as far as recording. Bounded
// because a popup that refuses to close must not spin forever.
std::function<void()> closeOpened(const std::shared_ptr<Walk> &walk)
{
    return [walk]() {
        for (const QPointer<QMenu> &menu : walk->opened) {
            if (menu && menu->isVisible())
                menu->close();
        }
        for (int guard = 0; guard < 8; ++guard) {
            QWidget *popup = QApplication::activePopupWidget();
            if (!popup)
                break;
            popup->close();
            if (QApplication::activePopupWidget() == popup)
                break;  // it is not going anywhere; leave it rather than spin
        }
    };
}

// The centre of an entry inside its menu, or a throw when the menu cannot show it. A QMenu that
// overflows the screen scrolls, and an entry outside the scrolled region has a geometry the user
// cannot click.
QPoint entryPoint(QMenu *menu, QAction *action)
{
    const QRect rect = menu->actionGeometry(action);
    if (rect.isEmpty() || !menu->rect().contains(rect.center())) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("the entry '%1' is scrolled out of its menu; "
                                          "trigger it with mode='synthetic'")
                               .arg(actionLabel(action)));
    }
    return rect.center();
}

} // namespace

namespace menu_walker {

void walkMenu(QMenuBar *bar, const QStringList &path,
              Dispatcher::Resolver resolve, Dispatcher::Rejecter reject)
{
    QList<ClickFlow::Fn> steps;
    const QPointer<QMenuBar> guard(bar);
    auto walk = std::make_shared<Walk>();

    // Open the top-level menu with a click on its bar entry. QMenuBar pops the menu up on the
    // press; nothing here blocks, because popup() runs no nested loop.
    steps.append([guard, walk, wanted = path.first().trimmed(),
                  lastStage = path.size() == 1]() {
        if (!guard)
            throw CommandError(ErrorCode::Stale, QStringLiteral("the menu bar was destroyed"));
        QAction *entry = findEntry(guard->actions(), wanted, QStringLiteral("the menu bar"));
        if (!entry->isEnabled()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("the menu entry '%1' is disabled").arg(wanted));
        }
        const QRect rect = guard->actionGeometry(entry);
        if (rect.isEmpty()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("'%1' has no geometry on the menu bar")
                                   .arg(wanted));
        }
        if (lastStage)
            noteLeaf(walk, entry);  // a bar entry with no menu is itself the action
        walk->current = entry;
        InputSynth::clickNative(guard, rect.center());
        return ClickFlow::Step::Next;
    });

    // For each deeper level: wait until the menu the previous click opened is on screen, and
    // only then look the next entry up inside it -- a menu that builds its entries in
    // aboutToShow ("Recent Files") has nothing to find until it is genuinely open.
    for (int i = 1; i < path.size(); ++i) {
        steps.append(levelStage(walk, path.at(i).trimmed(),
                                path.mid(0, i).join(QStringLiteral(" > ")),
                                i == path.size() - 1));
    }

    new ClickFlow(steps, [walk, resolve](const QVariant &) { resolve(walk->leaf); },
                  std::move(reject), closeOpened(walk),
                  QStringLiteral("a menu did not appear after being clicked (stage %1)"));
}

void walkContextMenu(QWidget *target, const QPoint &point, const QStringList &path,
                     Dispatcher::Resolver resolve, Dispatcher::Rejecter reject)
{
    QList<ClickFlow::Fn> steps;
    const QPointer<QWidget> guard(target);
    auto walk = std::make_shared<Walk>();

    steps.append([guard, point]() {
        if (!guard)
            throw CommandError(ErrorCode::Stale, QStringLiteral("the target was destroyed"));
        InputSynth::contextClickNative(guard, point);
        return ClickFlow::Step::Next;
    });

    // The menu the right-click opened is whatever popup now holds the grab -- a context menu is
    // application-built, so there is no widget relationship to follow to it.
    steps.append([walk, wanted = path.first().trimmed(), lastStage = path.size() == 1]() {
        auto *menu = qobject_cast<QMenu *>(QApplication::activePopupWidget());
        if (!menu || !menu->isVisible())
            return ClickFlow::Step::Wait;
        walk->opened.append(menu);
        QAction *entry = findEntry(menu->actions(), wanted, QStringLiteral("the context menu"));
        if (!entry->isEnabled()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("the menu entry '%1' is disabled").arg(wanted));
        }
        if (lastStage)
            noteLeaf(walk, entry);
        walk->current = entry;
        InputSynth::clickNative(menu, entryPoint(menu, entry));
        return ClickFlow::Step::Next;
    });

    for (int i = 1; i < path.size(); ++i) {
        steps.append(levelStage(walk, path.at(i).trimmed(),
                                path.mid(0, i).join(QStringLiteral(" > ")),
                                i == path.size() - 1));
    }

    new ClickFlow(steps, [walk, resolve](const QVariant &) { resolve(walk->leaf); },
                  std::move(reject), closeOpened(walk),
                  QStringLiteral("the context menu did not appear after the right-click "
                                 "(stage %1)"));
}

void selectComboEntry(QComboBox *combo, int index,
                      Dispatcher::Resolver resolve, Dispatcher::Rejecter reject)
{
    QList<ClickFlow::Fn> steps;
    const QPointer<QComboBox> guard(combo);

    // A click anywhere on a closed combo opens its popup.
    steps.append([guard]() {
        if (!guard)
            throw CommandError(ErrorCode::Stale, QStringLiteral("the combo box was destroyed"));
        InputSynth::clickNative(guard, guard->rect().center());
        return ClickFlow::Step::Next;
    });

    // Wait for the popup, then click the row in its view. The scroll is programmatic because
    // that is what the popup itself does to show the current entry; the choice is still a click.
    steps.append([guard, index]() {
        if (!guard)
            throw CommandError(ErrorCode::Stale, QStringLiteral("the combo box was destroyed"));
        QAbstractItemView *view = guard->view();
        if (!view || !view->isVisible())
            return ClickFlow::Step::Wait;
        const QModelIndex item =
            guard->model()->index(index, guard->modelColumn(), guard->rootModelIndex());
        view->scrollTo(item);
        const QRect rect = view->visualRect(item);
        if (rect.isEmpty())
            return ClickFlow::Step::Wait;
        InputSynth::clickNative(view->viewport(), rect.center());
        return ClickFlow::Step::Next;
    });

    auto cleanup = [guard]() {
        if (guard)
            guard->hidePopup();
    };

    new ClickFlow(steps, std::move(resolve), std::move(reject), cleanup,
                  QStringLiteral("the combo popup did not appear after being clicked (stage %1)"));
}

} // namespace menu_walker

} // namespace liberaqt
