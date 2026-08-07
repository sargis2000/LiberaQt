#pragma once

#include <QVariantList>
#include <QVariantMap>

class QObject;
class QWidget;
class QPoint;

namespace qtdriver {

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

    // TODO(m1): itemRect(), selectItem(), menuTrigger(), tabSelect()
};

} // namespace qtdriver
