#include "selector_engine.h"

#include "object_registry.h"

#include <QGuiApplication>
#include <QMetaObject>
#include <QMetaProperty>
#include <QRegularExpression>
#include <QVariant>
#include <QWindow>

#ifdef QTDRIVER_HAVE_QUICK
#  include "quick_backend.h"
#endif

namespace qtdriver {

Selector Selector::fromJson(const QVariantMap &json)
{
    Selector selector;
    selector.source = json.value(QStringLiteral("source")).toString();
    const QVariantList steps = json.value(QStringLiteral("steps")).toList();
    for (const QVariant &raw : steps) {
        const QVariantMap map = raw.toMap();
        Step step;
        step.type = map.value(QStringLiteral("type")).toString();
        step.exactType = map.value(QStringLiteral("exact_type")).toBool();
        step.objectName = map.value(QStringLiteral("objectName")).toString();
        step.directChild = map.value(QStringLiteral("direct_child")).toBool();
        if (map.contains(QStringLiteral("index"))) {
            step.index = map.value(QStringLiteral("index")).toInt();
            step.hasIndex = true;
        }
        for (const QVariant &a : map.value(QStringLiteral("attrs")).toList()) {
            const QVariantMap am = a.toMap();
            step.attrs.append(Attr{am.value(QStringLiteral("key")).toString(),
                                   am.value(QStringLiteral("op")).toString(),
                                   am.value(QStringLiteral("value")).toString()});
        }
        step.states = map.value(QStringLiteral("states")).toStringList();
        selector.steps.append(step);
    }
    return selector;
}

SelectorEngine::SelectorEngine(ObjectRegistry &registry)
    : m_registry(registry)
{
}

QString SelectorEngine::displayText(const QObject *object)
{
    static const char *keys[] = {"text", "title", "windowTitle", "plainText", "currentText",
                                 "displayText", "placeholderText"};
    for (const char *key : keys) {
        const QVariant value = object->property(key);
        if (value.isValid() && value.canConvert<QString>()) {
            QString text = value.toString();
            if (!text.isEmpty()) {
                text.remove(QLatin1Char('&'));
                return text.simplified();
            }
        }
    }
    return {};
}

bool SelectorEngine::matchesType(const QObject *object, const Step &step)
{
    if (step.type.isEmpty() || step.type == QLatin1String("*"))
        return true;

    const QMetaObject *meta = object->metaObject();
    const QByteArray wanted = step.type.toUtf8();

    if (step.exactType)
        return meta->className() == wanted;

    for (const QMetaObject *m = meta; m; m = m->superClass()) {
        if (wanted == m->className())
            return true;
    }

#ifdef QTDRIVER_HAVE_QUICK
    // QML types report a mangled C++ class name (Button_QMLTYPE_42). Match the declared QML type
    // name too, because that is what the test author sees in the .qml file.
    if (QuickBackend::qmlTypeName(object) == step.type)
        return true;
#endif
    return false;
}

bool SelectorEngine::matchesAttr(const QObject *object, const Attr &attr)
{
    QString actual;
    if (attr.key == QLatin1String("text"))
        actual = displayText(object);
    else if (attr.key == QLatin1String("objectName"))
        actual = object->objectName();
    else
        actual = object->property(attr.key.toUtf8().constData()).toString();

    const QString &expected = attr.value;
    if (attr.op == QLatin1String("="))
        return actual.simplified() == expected.simplified();
    if (attr.op == QLatin1String("!="))
        return actual.simplified() != expected.simplified();
    if (attr.op == QLatin1String("*="))
        return actual.contains(expected);
    if (attr.op == QLatin1String("^="))
        return actual.startsWith(expected);
    if (attr.op == QLatin1String("$="))
        return actual.endsWith(expected);
    if (attr.op == QLatin1String("~="))
        return QRegularExpression(expected).match(actual).hasMatch();
    return false;
}

bool SelectorEngine::matchesState(const QObject *object, const QString &state)
{
    const QVariant value = object->property(state.toUtf8().constData());
    return value.isValid() && value.toBool();
}

bool SelectorEngine::matchesStep(const QObject *object, const Step &step)
{
    if (!matchesType(object, step))
        return false;
    if (!step.objectName.isEmpty() && object->objectName() != step.objectName)
        return false;
    for (const Attr &attr : step.attrs) {
        if (attr.key.startsWith(QLatin1String("__")))
            continue; // client-side pseudo-attrs (e.g. __has_not); handled in m1
        if (!matchesAttr(object, attr))
            return false;
    }
    for (const QString &state : step.states) {
        if (!matchesState(object, state))
            return false;
    }
    return true;
}

QList<QObject *> SelectorEngine::rootObjects() const
{
    QList<QObject *> roots;
    const auto windows = QGuiApplication::topLevelWindows();
    for (QWindow *w : windows)
        roots.append(w);
    // TODO(m0): also enumerate QApplication::topLevelWidgets() and splice QQuickWindow content
    // items so widget and Quick trees appear as one tree (see ARCHITECTURE.md section 4).
    return roots;
}

QList<QObject *> SelectorEngine::childrenOf(QObject *parent) const
{
    QList<QObject *> result = parent->children();
#ifdef QTDRIVER_HAVE_QUICK
    QuickBackend::appendVisualChildren(parent, result);
#endif
    return result;
}

QList<QObject *> SelectorEngine::find(const Selector &selector, QObject *root, int limit)
{
    QList<QObject *> current = root ? QList<QObject *>{root} : rootObjects();

    for (int stepIndex = 0; stepIndex < selector.steps.size(); ++stepIndex) {
        const Step &step = selector.steps.at(stepIndex);
        QList<QObject *> next;

        for (QObject *scope : current) {
            // Depth-first, insertion-ordered walk. Deterministic by construction.
            QList<QObject *> stack = childrenOf(scope);
            while (!stack.isEmpty()) {
                QObject *candidate = stack.takeFirst();
                if (matchesStep(candidate, step))
                    next.append(candidate);
                if (!step.directChild) {
                    const QList<QObject *> grandChildren = childrenOf(candidate);
                    for (int i = grandChildren.size() - 1; i >= 0; --i)
                        stack.prepend(grandChildren.at(i));
                }
            }
        }

        if (step.hasIndex && !next.isEmpty()) {
            const int idx = step.index < 0 ? next.size() + step.index : step.index;
            next = (idx >= 0 && idx < next.size()) ? QList<QObject *>{next.at(idx)}
                                                   : QList<QObject *>{};
        }

        current = next;
        if (current.isEmpty())
            break;
    }

    if (limit > 0 && current.size() > limit)
        current = current.mid(0, limit);
    return current;
}

QVariantList SelectorEngine::nearMisses(const Selector &selector, QObject *root, int limit)
{
    QVariantList result;
    if (selector.steps.isEmpty())
        return result;

    // Relax the last step to type-only, then report what we found and how it differed.
    Selector relaxed = selector;
    Step &last = relaxed.steps.last();
    const QList<Attr> droppedAttrs = last.attrs;
    last.attrs.clear();
    last.states.clear();
    last.objectName.clear();
    last.hasIndex = false;

    for (QObject *candidate : find(relaxed, root, limit)) {
        QVariantMap entry;
        entry.insert(QStringLiteral("class"),
                     QString::fromUtf8(candidate->metaObject()->className()));
        entry.insert(QStringLiteral("objectName"), candidate->objectName());
        entry.insert(QStringLiteral("text"), displayText(candidate));
        QStringList failed;
        for (const Attr &attr : droppedAttrs) {
            if (!matchesAttr(candidate, attr))
                failed.append(attr.key);
        }
        entry.insert(QStringLiteral("failed_predicates"), failed);
        result.append(entry);
    }
    return result;
}

} // namespace qtdriver
