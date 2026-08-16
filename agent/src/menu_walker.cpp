#include "menu_walker.h"

#include "input_synth.h"

#include <QAbstractItemView>
#include <QAction>
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

    // Records what is being activated, read just before the final click -- afterwards a
    // checkable entry has already toggled.
    auto noteLeaf = [walk](QAction *leaf) {
        walk->leaf.insert(QStringLiteral("text"), actionLabel(leaf));
        walk->leaf.insert(QStringLiteral("enabled"), leaf->isEnabled());
        walk->leaf.insert(QStringLiteral("checked"), leaf->isChecked());
        walk->leaf.insert(QStringLiteral("clicked"), true);
    };

    // Open the top-level menu with a click on its bar entry. QMenuBar pops the menu up on the
    // press; nothing here blocks, because popup() runs no nested loop.
    steps.append([guard, walk, noteLeaf, wanted = path.first().trimmed(),
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
            noteLeaf(entry);  // a bar entry with no menu is itself the action
        walk->current = entry;
        InputSynth::clickNative(guard, rect.center());
        return ClickFlow::Step::Next;
    });

    // For each deeper level: wait until the menu the previous click opened is on screen, and
    // only then look the next entry up inside it. The lookup MUST come after the wait -- a menu
    // that builds its entries in aboutToShow ("Recent Files") has nothing to find until it is
    // genuinely open. Clicking an entry that owns a submenu opens that submenu, which is what
    // the following stage waits for; clicking the final entry activates it.
    for (int i = 1; i < path.size(); ++i) {
        steps.append([walk, noteLeaf, wanted = path.at(i).trimmed(),
                      context = QStringList(path.mid(0, i)).join(QStringLiteral(" > ")),
                      lastStage = i == path.size() - 1]() {
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
                noteLeaf(entry);
            walk->current = entry;
            InputSynth::clickNative(menu, entryPoint(menu, entry));
            return ClickFlow::Step::Next;
        });
    }

    // On failure, close whatever got opened: a test that could not walk the menu should not
    // leave the application with a menu hanging open for every later action to fight with.
    auto cleanup = [walk]() {
        for (const QPointer<QMenu> &menu : walk->opened) {
            if (menu && menu->isVisible())
                menu->hide();
        }
    };

    new ClickFlow(steps, [walk, resolve](const QVariant &) { resolve(walk->leaf); },
                  std::move(reject), cleanup,
                  QStringLiteral("a menu did not appear after being clicked (stage %1)"));
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
