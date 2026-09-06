// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#include "idle_tracker.h"

#include <QAbstractAnimation>
#include <QCoreApplication>
#include <QDateTime>
#include <QTimer>

namespace liberaqt {

IdleTracker::IdleTracker(QObject *parent)
    : QObject(parent)
{
}

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

void IdleTracker::waitForIdle(int quietMs, bool animations, bool network, int timeoutMs, Done done)
{
    m_quietMs = quietMs;
    m_timeoutMs = timeoutMs;
    m_animations = animations;
    m_network = network;
    m_done = std::move(done);

    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    m_startedAt = now;
    m_quietSince = now;
    m_lastTickAt = now;

    QTimer::singleShot(0, this, &IdleTracker::tick);
}

void IdleTracker::tick()
{
    const qint64 now = QDateTime::currentMSecsSinceEpoch();

    // How late was this zero-delay tick? A prompt tick means everything queued ahead of it had
    // already been delivered; a late one means the loop is still working through a backlog.
    const bool congested = (now - m_lastTickAt) > LateMs;
    m_lastTickAt = now;

    const bool busy = congested
                      || (m_animations && hasRunningAnimations())
                      || (m_network && hasPendingNetwork());
    if (busy)
        m_quietSince = now;

    if (!busy && (now - m_quietSince) >= m_quietMs) {
        // One more turn so deferred deletes and queued connections posted by the last events
        // have run before we call the UI settled.
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        const Done done = m_done;
        m_done = nullptr;
        if (done)
            done(static_cast<int>(now - m_startedAt));
        deleteLater();
        return;
    }

    if ((now - m_startedAt) >= m_timeoutMs) {
        const Done done = m_done;
        m_done = nullptr;
        if (done)
            done(-1);
        deleteLater();
        return;
    }

    QTimer::singleShot(0, this, &IdleTracker::tick);
}

} // namespace liberaqt
