#include "quick_backend.h"

#include "dispatcher.h"
#include "object_registry.h"

#include <QQmlContext>
#include <QQmlEngine>
#include <QQmlExpression>
#include <QQuickItem>
#include <QQuickWindow>

namespace qtdriver {

QString QuickBackend::qmlTypeName(const QObject *object)
{
    QString name = QString::fromUtf8(object->metaObject()->className());
    // QML-declared types are mangled as Foo_QMLTYPE_17 or Foo_QMLBASE_3.
    const int marker = name.indexOf(QLatin1String("_QML"));
    if (marker > 0)
        name.truncate(marker);
    return name;
}

QString QuickBackend::qmlId(const QObject *object)
{
    QQmlContext *context = QQmlEngine::contextForObject(object);
    if (!context)
        return {};
    return context->nameForObject(const_cast<QObject *>(object));
}

void QuickBackend::appendVisualChildren(QObject *parent, QList<QObject *> &out)
{
    if (auto *item = qobject_cast<QQuickItem *>(parent)) {
        const auto items = item->childItems();
        for (QQuickItem *child : items) {
            if (!out.contains(child))
                out.append(child);
        }
        return;
    }
    if (auto *window = qobject_cast<QQuickWindow *>(parent)) {
        if (QQuickItem *content = window->contentItem()) {
            if (!out.contains(content))
                out.append(content);
        }
    }
}

QVariantMap QuickBackend::describe(QObject *object, ObjectRegistry &registry)
{
    QVariantMap info;
    info.insert(QStringLiteral("handle"), registry.handleFor(object));
    info.insert(QStringLiteral("class"), qmlTypeName(object));
    info.insert(QStringLiteral("qmlId"), qmlId(object));

    if (auto *item = qobject_cast<QQuickItem *>(object)) {
        const QPointF scenePos = item->mapToScene(QPointF(0, 0));
        info.insert(QStringLiteral("geometry"),
                    QVariantList{scenePos.x(), scenePos.y(), item->width(), item->height()});
        info.insert(QStringLiteral("visible"), item->isVisible());
        info.insert(QStringLiteral("enabled"), item->isEnabled());
        info.insert(QStringLiteral("focused"), item->hasActiveFocus());
    }
    return info;
}

QVariantMap QuickBackend::evaluate(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolve(params.value(QStringLiteral("handle")).toString());
    QQmlContext *context = QQmlEngine::contextForObject(object);
    if (!context) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("object has no QML context"));
    }

    QQmlExpression expression(context, object,
                              params.value(QStringLiteral("expression")).toString());
    bool failed = false;
    const QVariant value = expression.evaluate(&failed);
    if (failed || expression.hasError()) {
        throw CommandError(ErrorCode::Internal,
                           expression.error().toString());
    }

    QVariantMap out;
    out.insert(QStringLiteral("value"), value);
    return out;
}

QVariantMap QuickBackend::listViewItem(ObjectRegistry &registry, const QVariantMap &params)
{
    Q_UNUSED(registry)
    Q_UNUSED(params)
    // TODO(m2): call positionViewAtIndex() on the view, spin the event loop until the delegate
    // exists, then return its handle. Virtualised views do not have delegates for off-screen rows.
    throw CommandError(ErrorCode::Unsupported,
                       QStringLiteral("quick.list_view_item lands in milestone 2"));
}

} // namespace qtdriver
