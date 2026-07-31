#include "object_registry.h"

#include "dispatcher.h"

namespace qtdriver {

ObjectRegistry::ObjectRegistry() = default;

QString ObjectRegistry::handleFor(QObject *object)
{
    if (!object)
        return {};

    const auto existing = m_byObject.constFind(object);
    if (existing != m_byObject.constEnd())
        return *existing;

    const QString handle = QStringLiteral("o%1").arg(m_nextId++);
    m_byHandle.insert(handle, QPointer<QObject>(object));
    m_byObject.insert(object, handle);

    connect(object, &QObject::destroyed, this, [this, handle](QObject *dead) {
        m_byHandle.remove(handle);
        m_byObject.remove(dead);
        emit objectDestroyed(handle);
    });

    return handle;
}

QObject *ObjectRegistry::resolveOrNull(const QString &handle) const
{
    if (handle.isEmpty())
        return nullptr;
    const auto it = m_byHandle.constFind(handle);
    return it == m_byHandle.constEnd() ? nullptr : it->data();
}

QObject *ObjectRegistry::resolve(const QString &handle) const
{
    QObject *object = resolveOrNull(handle);
    if (!object) {
        throw CommandError(ErrorCode::Stale,
                           QStringLiteral("handle '%1' no longer refers to a live object")
                               .arg(handle));
    }
    return object;
}

void ObjectRegistry::forget(QObject *object)
{
    const QString handle = m_byObject.take(object);
    if (!handle.isEmpty())
        m_byHandle.remove(handle);
}

} // namespace qtdriver
