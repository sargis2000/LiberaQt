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

    // ---------------------------------------------------------------- item views
    //
    // A cell is not a QObject, so it cannot have a handle of its own. Instead a cell is addressed
    // by a *composite* handle, "<view>~<row>~<column>", which ObjectRegistry resolves to the view
    // while the suffix says where inside it. That keeps every existing command working on such a
    // handle -- clicking one clicks the cell, describing one describes the cell.
    static QVariantMap itemRect(QObject *object, const QVariantMap &params,
                                ObjectRegistry &registry);
    static QVariantMap selectItem(QObject *object, const QVariantMap &params);
    static QVariantMap tabSelect(QObject *object, const QVariantMap &params);

    // Splits "o17~0/4~2" into its parts: the view, the chain of rows from the root down to the
    // item, and the column. A chain rather than a single row because a tree keeps children under
    // their parent, so "row 4" is meaningless without knowing whose row 4 it is.
    // False when the handle names a plain object.
    static bool splitItemHandle(const QString &handle, QString *base, QList<int> *rows,
                                int *column);

    // Interaction point for a handle, which is the cell centre when the handle names one.
    static bool interactionPointFor(QObject *object, const QString &handle, QPoint *out);

    // Describes the cell a composite handle names, rather than the view it lives in.
    static QVariantMap describeItem(QObject *object, const QString &handle,
                                    ObjectRegistry &registry);

    // ---------------------------------------------------------------- menus
    //
    // Walks a "File > Export > PDF..." path from the window's menu bar. Triggering is queued: a
    // menu action commonly opens a modal dialog, and a direct call would not return until that
    // dialog closed, stranding the reply.
    static QVariantMap menuTrigger(QObject *window, const QVariantMap &params);

    // Every Q_PROPERTY the class exposes, so a test can discover what an unfamiliar widget offers.
    static QVariantList listProperties(QObject *object);
};

} // namespace liberaqt
