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

void walkMenu(QMenuBar *bar, const QList<QAction *> &chain,
              Dispatcher::Resolver resolve, Dispatcher::Rejecter reject)
{
    QList<ClickFlow::Fn> steps;
    const QPointer<QMenuBar> guard(bar);

    // Open the top-level menu with a click on its bar entry. QMenuBar pops the menu up on the
    // press; nothing here blocks, because popup() runs no nested loop.
    steps.append([guard, first = chain.first()]() {
        if (!guard)
            throw CommandError(ErrorCode::Stale, QStringLiteral("the menu bar was destroyed"));
        const QRect rect = guard->actionGeometry(first);
        if (rect.isEmpty()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("'%1' has no geometry on the menu bar")
                                   .arg(actionLabel(first)));
        }
        InputSynth::clickNative(guard, rect.center());
        return ClickFlow::Step::Next;
    });

    // For each deeper level: wait until the menu the previous click opened is on screen, then
    // click the next entry inside it. Clicking an entry that owns a submenu opens that submenu,
    // which is what the following stage waits for; clicking the final entry activates it.
    for (int i = 1; i < chain.size(); ++i) {
        QAction *parent = chain.at(i - 1);
        QAction *entry = chain.at(i);
        steps.append([parent, entry]() {
            QMenu *menu = parent->menu();
            if (!menu)
                throw CommandError(ErrorCode::Stale, QStringLiteral("a submenu disappeared"));
            if (!menu->isVisible())
                return ClickFlow::Step::Wait;
            InputSynth::clickNative(menu, entryPoint(menu, entry));
            return ClickFlow::Step::Next;
        });
    }

    // On failure, close whatever got opened: a test that could not walk the menu should not
    // leave the application with a menu hanging open for every later action to fight with.
    auto cleanup = [chain]() {
        for (QAction *action : chain) {
            if (QMenu *menu = action->menu(); menu && menu->isVisible())
                menu->hide();
        }
    };

    new ClickFlow(steps, std::move(resolve), std::move(reject), cleanup,
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
