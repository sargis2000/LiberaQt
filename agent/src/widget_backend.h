// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#pragma once

#include <QList>
#include <QVariantList>
#include <QVariantMap>

class QAction;
class QComboBox;
class QMenuBar;
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

    // The accessibility tree beneath an object: what a screen reader would see.
    //
    // The one general way into a widget that paints its own contents. Qt's accessibility
    // framework exists so such a widget can describe what it drew -- names, roles, and a rect
    // per element -- without exposing any QObjects, which is exactly the situation on a
    // schematic canvas or a chart. Coordinates come back in *screen* space, as QAccessible
    // reports them.
    //
    // Only as good as the widget's own implementation: a widget that implements nothing reports
    // itself and no children, and that is a real answer rather than a failure.
    static QVariantMap accessibleTree(QObject *object, int depth);

    // Empty string means "actionable". Otherwise a human-readable reason, which becomes the
    // NotActionableError message on the Python side. With `nativeInput` the check also demands
    // the object be *reachable* -- not behind a modal dialog, not covered by another widget,
    // not parked outside its window -- naming what is in the way, because that is usually the
    // actual bug the test found. Synthetic delivery skips those, deliberately: bypassing them
    // is what it is for.
    static QString actionabilityProblem(QObject *object, bool nativeInput = false);

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
    // Where a highlight should be drawn for a handle: the top-level widget to parent an
    // overlay to, and the rectangle in that widget's coordinates. A composite handle resolves
    // to the cell rather than to the whole view.
    static bool highlightTarget(QObject *object, const QString &handle,
                                QWidget **topLevel, QRect *rect);

    static QVariantMap describeItem(QObject *object, const QString &handle,
                                    ObjectRegistry &registry);

    // ---------------------------------------------------------------- menus
    //
    // The window's menu bar, or a throw when it has none.
    static QMenuBar *menuBarOf(QObject *window);

    // Resolves a "File > Export > PDF..." path from the window's menu bar into the chain of
    // actions it names, one per level -- all of it *before* any menu opens, which is what the
    // probe and the synthetic queued trigger need. The native path deliberately does not use
    // this: menu_walker resolves each level after clicking its menu open, so entries created in
    // aboutToShow are addressable there and only there.
    static QList<QAction *> menuPath(QObject *window, const QVariantMap &params,
                                     QMenuBar **barOut);

    // The row a combo-box selection names, by "text" or "index"/"row", with the available
    // entries in the error when it names none.
    static int comboEntry(QComboBox *combo, const QVariantMap &params);

    // A named clickable sub-part of a widget -- a spin box's "spin_up" / "spin_down" arrows,
    // located through QStyle so the point is right for whatever style the application uses.
    // False when the widget has no such part.
    static bool partPoint(QWidget *widget, const QString &part, QPoint *out);

    // Every Q_PROPERTY the class exposes, so a test can discover what an unfamiliar widget offers.
    static QVariantList listProperties(QObject *object);
};

} // namespace liberaqt
