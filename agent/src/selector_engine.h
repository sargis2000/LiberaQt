#pragma once

#include <QStringList>
#include <QList>
#include <QString>
#include <QVariantList>
#include <QVariantMap>

class QObject;

namespace qtdriver {

class ObjectRegistry;

struct Attr
{
    QString key;
    QString op;     // "=", "*=", "^=", "$=", "~=", "!="
    QString value;
};

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

private:
    static bool matchesType(const QObject *object, const Step &step);
    static bool matchesAttr(const QObject *object, const Attr &attr);
    static bool matchesState(const QObject *object, const QString &state);

    QList<QObject *> childrenOf(QObject *parent) const;
    QList<QObject *> rootObjects() const;

    ObjectRegistry &m_registry;
};

} // namespace qtdriver
