// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#pragma once

#include <memory>
#include <QStringList>
#include <QList>
#include <QString>
#include <QVariantList>
#include <QVariantMap>

class QObject;

namespace liberaqt {

class ObjectRegistry;

// Marks an object the agent created for its own purposes. Anything carrying it is invisible to
// the selector engine and to object.tree, so the tooling cannot perturb what it is inspecting.
#define LIBERAQT_INTERNAL_PROPERTY "__liberaqt_internal"

struct Attr
{
    QString key;
    QString op;     // "=", "*=", "^=", "$=", "~=", "!="
    QString value;
};

// Declared ahead of Step so that a step can carry a nested selector for :has(...). Held by
// pointer because the two types are mutually recursive: a Selector owns Steps, and a Step may
// own a Selector.
struct Selector;

struct Step
{
    QString type;              // QMetaObject class name or QML type name; "*" or empty = any
    bool exactType = false;    // trailing '!' in the selector -> no inherits() walk
    QString objectName;
    QList<Attr> attrs;
    QStringList states;        // visible / enabled / checked / focused
    int index = -1;            // :nth(N); -1 = unset, -2 encodes :last
    bool directChild = false;
    bool hasIndex = false;

    // :has(...) -- keep only objects that contain a match for this selector somewhere beneath
    // them. Null when the step carries no :has. Evaluated by the engine rather than by
    // matchesStep(), because deciding it means searching a subtree.
    std::shared_ptr<Selector> has;

    // :parent(...) -- keep only objects with an ancestor matching this selector. The mirror of
    // :has(), which looks downwards. Null when the step carries no :parent.
    std::shared_ptr<Selector> parent;
};

struct Selector
{
    QList<Step> steps;
    QString source;

    static Selector fromJson(const QVariantMap &json);
};

// Evaluates a parsed selector against the object tree.
//
// Traversal is depth-first in child-insertion order, which makes :nth(i) mean the same thing on
// every run -- non-negotiable for test stability.
class SelectorEngine
{
public:
    explicit SelectorEngine(ObjectRegistry &registry);

    QList<QObject *> find(const Selector &selector, QObject *root, int limit = 0);

    // Objects that matched some but not all predicates. Attached to `not_found` errors so a
    // failure explains itself: "found 3 QPushButton, none with text 'OK'; closest was 'Ok'".
    QVariantList nearMisses(const Selector &selector, QObject *root, int limit);

    // Normalised display text: text / title / windowTitle / plainText / currentText,
    // whichever the class provides, with '&' mnemonics stripped and whitespace collapsed.
    static QString displayText(const QObject *object);

    static bool matchesStep(const QObject *object, const Step &step);

    // Whether one object satisfies a whole selector, rather than searching for the objects that
    // do. The question `:has()` and `object.ancestor` both need: given this object, does it look
    // like what was asked for? A multi-step selector is satisfied when its last step matches the
    // object and the earlier steps match objects above it.
    //
    // `>` between steps of such a selector is treated as plain descendancy: the walk upwards
    // does not track how far each hop went. Single-step selectors -- the normal case for
    // ancestor(), as in ancestor("QDockWidget") -- are unaffected.
    bool matchesSelector(QObject *object, const Selector &want);

    // The children of an object as the tooling should see them: QObject children, plus the
    // visual children Quick keeps outside that hierarchy -- a QQuickWindow's contentItem, and a
    // QQuickItem's childItems(). Public and static because object.tree needs exactly the same
    // walk; when it had its own, widget and Quick trees disagreed about what existed.
    static QList<QObject *> visualChildren(QObject *parent);

private:
    static bool matchesType(const QObject *object, const Step &step);
    static bool matchesAttr(const QObject *object, const Attr &attr);
    static bool matchesState(const QObject *object, const QString &state);

    // :has(...) for one candidate. Not static and not part of matchesStep(): it runs a nested
    // find() beneath the candidate, so it needs the engine.
    bool matchesHas(QObject *object, const Step &step);

    // :parent(...) for one candidate, and the recursive walk that backs it.
    bool matchesParent(QObject *object, const Step &step);
    bool matchesParentChain(QObject *object, const Selector &want, int stepIndex);

    QList<QObject *> rootObjects() const;

    ObjectRegistry &m_registry;
};

} // namespace liberaqt
