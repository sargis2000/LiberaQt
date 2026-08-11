#pragma once

#include <QObject>
#include <functional>

namespace liberaqt {

// "Is the UI settled?" -- the single most important heuristic in a GUI automation tool.
//
// Layered, cheapest check first:
//   1. The event queue has been empty for `quietMs` consecutive milliseconds.
//   2. No QAbstractAnimation is running (covers QPropertyAnimation and Quick transitions).
//   3. Optionally: no QNetworkReply is in flight (opt-in; requires the app to use QNetworkAccessManager).
//   4. One extra event loop turn, so deferred deletes and queued connections have run.
//
// Each layer is independently disableable, because every app has one that does not apply --
// a permanently-running animation is common, and a policy that cannot be relaxed is a policy that
// gets worked around with sleep().
//
// The wait is *driven by* the event loop, never pumps it. Pumping (processEvents) from inside a
// command handler strands the reply whenever the pump makes the application enter a nested loop
// of its own -- a modal QDialog::exec() during startup is enough, and real applications do that.
// A zero-delay timer instead runs only once everything queued ahead of it has been delivered, so
// each tick is itself the proof that the queue drained, and it keeps ticking inside a nested loop.
class IdleTracker : public QObject
{
    Q_OBJECT
public:
    // Called with the milliseconds waited, or -1 if the UI never settled within the timeout.
    using Done = std::function<void(int waitedMs)>;

    explicit IdleTracker(QObject *parent = nullptr);

    // Returns immediately. `done` is invoked later, from the event loop.
    void waitForIdle(int quietMs, bool animations, bool network, int timeoutMs, Done done);

    bool hasRunningAnimations() const;
    bool hasPendingNetwork() const;

private:
    void tick();

    // A tick arriving more than this late means the loop is still congested, so the quiet window
    // starts over. Generous enough not to trip on ordinary timer jitter under load.
    static constexpr int LateMs = 8;

    int m_quietMs = 0;
    int m_timeoutMs = 0;
    bool m_animations = false;
    bool m_network = false;
    qint64 m_startedAt = 0;
    qint64 m_quietSince = 0;
    qint64 m_lastTickAt = 0;
    Done m_done;
};

} // namespace liberaqt
