#pragma once

#include <QObject>

namespace qtdriver {

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
class IdleTracker : public QObject
{
    Q_OBJECT
public:
    IdleTracker();

    // Blocks (spinning a nested event loop) until idle or timeout. Returns ms actually waited,
    // or -1 on timeout.
    int waitForIdle(int quietMs, bool animations, bool network, int timeoutMs);

    bool hasRunningAnimations() const;
    bool hasPendingNetwork() const;

private:
    bool m_installed = false;
};

} // namespace qtdriver
