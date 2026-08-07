#pragma once

#include <QVariantList>
#include <QVariantMap>

class QObject;
class QWidget;
class QPoint;

namespace liberaqt {

class ObjectRegistry;

// QWidget-specific introspection: window enumeration, geometry mapping, actionability, item views.
class WidgetBackend
{
public:
    static QVariantList listWindows(ObjectRegistry &registry);
    static QVariantMap describe(QObject *object, ObjectRegistry &registry);
    static QVariantMap dumpTree(QObject *root, ObjectRegistry &registry, int depth,
                                bool visualOnly);

    // Empty string means "actionable". Otherwise a human-readable reason, which becomes the
    // NotActionableError message on the Python side.
    static QString actionabilityProblem(QObject *object);

    // Centre of the object in window coordinates, adjusted if the centre is obscured.
    static bool interactionPoint(QObject *object, QPoint *out);

    // Read a model-backed view's contents: {"rows": [{header: value, ...}, ...]}.
    static QVariantMap modelData(QObject *object, int maxRows = -1);

    // Synthetic invoke targets, named with a "__" prefix by convention.
    //
    // Some operations tests need are plain public functions rather than slots, so moc never sees
    // them and object.invoke cannot reach them however they are spelled. Rather than widen the
    // command surface for each one, they are exposed as named operations here.
    static QVariant synthetic(QObject *object, const QString &name, const QVariantList &args);

    // TODO(m1): itemRect(), selectItem(), menuTrigger(), tabSelect()
};

} // namespace liberaqt
