#include "idle_tracker.h"

#include <QAbstractAnimation>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>

namespace liberaqt {

IdleTracker::IdleTracker() = default;

bool IdleTracker::hasRunningAnimations() const
{
    // TODO(m2): walk QUnifiedTimer's registered animations. Qt exposes no public API for this,
    // so the real implementation tracks QAbstractAnimation::stateChanged via a global event
    // filter on QEvent::ChildAdded, which is intrusive but reliable.
    return false;
}

bool IdleTracker::hasPendingNetwork() const
{
    // TODO(m4): opt-in QNetworkAccessManager instrumentation.
    return false;
}

int IdleTracker::waitForIdle(int quietMs, bool animations, bool network, int timeoutMs)
{
    QElapsedTimer total;
    total.start();

    QElapsedTimer quiet;
    quiet.start();

    while (total.elapsed() < timeoutMs) {
        // Drain everything currently queued, including deferred deletes.
        QCoreApplication::processEvents(QEventLoop::AllEvents, 10);
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);

        const bool busy = (animations && hasRunningAnimations())
                          || (network && hasPendingNetwork());
        if (busy) {
            quiet.restart();
            continue;
        }
        if (quiet.elapsed() >= quietMs)
            return static_cast<int>(total.elapsed());
    }
    return -1;
}

} // namespace liberaqt
