// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#pragma once

#include <QObject>
#include <QString>
#include <functional>

namespace liberaqt {

// Waits for one emission of a signal named at runtime, then reports once.
//
// Connecting to a signal whose signature is only known as a string needs a real slot on the
// receiving side: QObject::connect's QMetaMethod overload takes a QMetaMethod for both ends, and
// a lambda cannot be one. Qt permits a slot to take fewer arguments than the signal, so a no-arg
// slot receives any signal at all -- which is exactly what "wait until this fires" needs.
//
// Deletes itself once it has reported, whether that was the signal or the timeout, and reports
// exactly once either way.
class SignalWaiter : public QObject
{
    Q_OBJECT
public:
    // Called with true when the signal arrived, false on timeout.
    using Done = std::function<void(bool emitted)>;

    // Returns false when the object has no such signal; `available` then lists the ones it does
    // have, which is the difference between a dead end and a fixable typo.
    static bool start(QObject *target, const QString &signal, int timeoutMs, Done done,
                      QString *available);

private slots:
    void onSignal();
    void onTimeout();

private:
    SignalWaiter(QObject *parent, Done done);
    void finish(bool emitted);

    Done m_done;
    bool m_reported = false;
};

} // namespace liberaqt
