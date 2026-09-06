// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#include "signal_waiter.h"

#include <QCoreApplication>
#include <QMetaMethod>
#include <QMetaObject>
#include <QStringList>
#include <QTimer>

namespace liberaqt {

SignalWaiter::SignalWaiter(QObject *parent, Done done)
    : QObject(parent)
    , m_done(std::move(done))
{
}

bool SignalWaiter::start(QObject *target, const QString &signal, int timeoutMs, Done done,
                         QString *available)
{
    const QMetaObject *meta = target->metaObject();
    const QByteArray wanted = signal.toUtf8();

    int index = meta->indexOfSignal(QMetaObject::normalizedSignature(wanted.constData()));
    if (index < 0) {
        // Callers write "clicked", not "clicked(bool)", so fall back to matching the bare name.
        for (int i = 0; i < meta->methodCount() && index < 0; ++i) {
            const QMetaMethod method = meta->method(i);
            if (method.methodType() == QMetaMethod::Signal && method.name() == wanted)
                index = i;
        }
    }
    if (index < 0) {
        if (available) {
            QStringList names;
            for (int i = 0; i < meta->methodCount(); ++i) {
                const QMetaMethod method = meta->method(i);
                if (method.methodType() == QMetaMethod::Signal)
                    names.append(QString::fromUtf8(method.name()));
            }
            names.removeDuplicates();
            *available = names.join(QStringLiteral(", "));
        }
        return false;
    }

    // Parented to the target so that an object destroyed mid-wait takes its waiter with it.
    auto *waiter = new SignalWaiter(target, std::move(done));
    const QMetaMethod slot = waiter->metaObject()->method(
        waiter->metaObject()->indexOfSlot("onSignal()"));
    QObject::connect(target, meta->method(index), waiter, slot);

    auto *timer = new QTimer(waiter);
    timer->setSingleShot(true);
    QObject::connect(timer, &QTimer::timeout, waiter, &SignalWaiter::onTimeout);
    timer->start(timeoutMs);
    return true;
}

void SignalWaiter::onSignal()
{
    finish(true);
}

void SignalWaiter::onTimeout()
{
    finish(false);
}

void SignalWaiter::finish(bool emitted)
{
    // A signal can arrive while the timeout is already queued, so the first answer wins.
    if (m_reported)
        return;
    m_reported = true;
    const Done done = m_done;
    m_done = nullptr;
    deleteLater();
    if (done)
        done(emitted);
}

} // namespace liberaqt
