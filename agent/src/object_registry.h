#pragma once

#include <QHash>
#include <QObject>
#include <QPointer>
#include <QString>

namespace liberaqt {

// Maps QObject* <-> opaque string handles ("o17"). Handles are stable for the lifetime of the
// object and are invalidated automatically when it is destroyed, so a stale handle produces a
// clean `stale` protocol error instead of a use-after-free.
class ObjectRegistry : public QObject
{
    Q_OBJECT
public:
    ObjectRegistry();

    QString handleFor(QObject *object);
    QObject *resolve(const QString &handle) const;      // throws CommandError on miss
    QObject *resolveOrNull(const QString &handle) const; // empty handle -> nullptr

    void forget(QObject *object);
    int size() const { return m_byHandle.size(); }

signals:
    void objectDestroyed(const QString &handle);

private:
    QHash<QString, QPointer<QObject>> m_byHandle;
    QHash<const QObject *, QString> m_byObject;
    quint64 m_nextId = 1;
};

} // namespace liberaqt
