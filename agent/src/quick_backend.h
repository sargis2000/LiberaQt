#pragma once

#include <QList>
#include <QString>
#include <QVariantMap>

class QObject;

namespace liberaqt {

class ObjectRegistry;

// Qt Quick support. Compiled only when Qt Quick was found at build time, so an agent built for a
// widgets-only deployment stays small and has no QML dependency.
class QuickBackend
{
public:
    // The QML type name as written in the .qml file ("Button"), not the mangled C++ class
    // ("Button_QMLTYPE_42"). Test authors think in QML type names.
    static QString qmlTypeName(const QObject *object);

    // The `id` an item was declared with, resolved through its QQmlContext.
    static QString qmlId(const QObject *object);

    // Splices QQuickItem::childItems() into the QObject child list, so widget and Quick trees are
    // traversed as one tree by the selector engine.
    static void appendVisualChildren(QObject *parent, QList<QObject *> &out);

    static QVariantMap describe(QObject *object, ObjectRegistry &registry);
    static QVariantMap evaluate(ObjectRegistry &registry, const QVariantMap &params);

    // Forces a virtualised delegate to be created before returning it. Without this, asking for
    // "row 500" of a ListView returns not_found even though the data is there -- the single most
    // confusing QML testing failure.
    static QVariantMap listViewItem(ObjectRegistry &registry, const QVariantMap &params);
};

} // namespace liberaqt
