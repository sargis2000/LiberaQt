#include "input_synth.h"

#include "dispatcher.h"
#include "object_registry.h"
#include "widget_backend.h"

#include <QAbstractScrollArea>
#include <QApplication>
#include <QKeyEvent>
#include <QKeySequence>
#include <QMouseEvent>
#include <QPoint>
#include <QWheelEvent>
#include <QWidget>
#include <QWindow>

namespace liberaqt {

namespace {

Qt::MouseButton parseButton(const QString &name)
{
    if (name == QLatin1String("right"))
        return Qt::RightButton;
    if (name == QLatin1String("middle"))
        return Qt::MiddleButton;
    return Qt::LeftButton;
}

Qt::KeyboardModifiers parseModifiers(const QVariantList &names)
{
    Qt::KeyboardModifiers mods = Qt::NoModifier;
    for (const QVariant &raw : names) {
        const QString name = raw.toString().toLower();
        if (name == QLatin1String("ctrl") || name == QLatin1String("control"))
            mods |= Qt::ControlModifier;
        else if (name == QLatin1String("shift"))
            mods |= Qt::ShiftModifier;
        else if (name == QLatin1String("alt"))
            mods |= Qt::AltModifier;
        else if (name == QLatin1String("meta"))
            mods |= Qt::MetaModifier;
    }
    return mods;
}

QWidget *targetWidget(ObjectRegistry &registry, const QVariantMap &params, QPoint *point)
{
    QObject *object = registry.resolve(params.value(QStringLiteral("handle")).toString());
    auto *widget = qobject_cast<QWidget *>(object);
    if (!widget) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("input on non-widget objects lands in milestone 2 "
                                          "(Qt Quick backend)"));
    }
    const QVariant pos = params.value(QStringLiteral("pos"));
    if (pos.isValid() && !pos.isNull()) {
        const QVariantList xy = pos.toList();
        *point = QPoint(xy.value(0).toInt(), xy.value(1).toInt());
    } else if (!WidgetBackend::interactionPointFor(
                   widget, params.value(QStringLiteral("handle")).toString(), point)) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("cannot compute an interaction point"));
    }
    return widget;
}

// Shared by key(), press() and release(): a single chord out of a key sequence.
void parseChord(const QString &spec, int *keyCode, Qt::KeyboardModifiers *mods)
{
    const QKeySequence sequence(spec);
    if (sequence.isEmpty()) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("cannot parse key sequence '%1'").arg(spec));
    }
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    const QKeyCombination combo = sequence[0];
    *keyCode = combo.key();
    *mods = combo.keyboardModifiers();
#else
    const int raw = sequence[0];
    *keyCode = raw & ~Qt::KeyboardModifierMask;
    *mods = Qt::KeyboardModifiers(raw & Qt::KeyboardModifierMask);
#endif
}

QWidget *keyTarget(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolveOrNull(params.value(QStringLiteral("handle")).toString());
    QWidget *target = qobject_cast<QWidget *>(object);
    if (!target)
        target = QApplication::focusWidget();
    if (!target) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("no focused widget to receive the key"));
    }
    return target;
}

} // namespace

QVariantMap InputSynth::click(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);

    const Qt::MouseButton button = parseButton(params.value(QStringLiteral("button")).toString());
    const Qt::KeyboardModifiers mods =
        parseModifiers(params.value(QStringLiteral("modifiers")).toList());
    const int count = qMax(1, params.value(QStringLiteral("count"), 1).toInt());

    const QPoint global = widget->mapToGlobal(point);

    for (int i = 0; i < count; ++i) {
        const QEvent::Type pressType = (i == 1) ? QEvent::MouseButtonDblClick
                                                : QEvent::MouseButtonPress;
        QMouseEvent press(pressType, point, global, button, button, mods);
        QApplication::sendEvent(widget, &press);
        QMouseEvent release(QEvent::MouseButtonRelease, point, global, button, Qt::NoButton, mods);
        QApplication::sendEvent(widget, &release);
    }

    // TODO(m1): route through QWindowSystemInterface::handleMouseEvent instead, so that grabs,
    // hover tracking and native event filters behave exactly as they do for real input.

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::hover(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);
    QMouseEvent move(QEvent::MouseMove, point, widget->mapToGlobal(point),
                     Qt::NoButton, Qt::NoButton, Qt::NoModifier);
    QApplication::sendEvent(widget, &move);
    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::key(ObjectRegistry &registry, const QVariantMap &params)
{
    const QString spec = params.value(QStringLiteral("key")).toString();
    const QKeySequence sequence(spec);
    if (sequence.isEmpty()) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("cannot parse key sequence '%1'").arg(spec));
    }

    QObject *object = registry.resolveOrNull(params.value(QStringLiteral("handle")).toString());
    QWidget *target = qobject_cast<QWidget *>(object);
    if (!target)
        target = QApplication::focusWidget();
    if (!target) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("no focused widget to receive the key"));
    }

    // TODO(m1): decompose the sequence properly (modifiers down, key, modifiers up) and support
    // multi-chord sequences. This handles the single-chord case only.
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    const QKeyCombination combo = sequence[0];
    const int keyCode = combo.key();
    const Qt::KeyboardModifiers mods = combo.keyboardModifiers();
#else
    const int raw = sequence[0];
    const int keyCode = raw & ~Qt::KeyboardModifierMask;
    const Qt::KeyboardModifiers mods(raw & Qt::KeyboardModifierMask);
#endif

    const int count = qMax(1, params.value(QStringLiteral("count"), 1).toInt());
    for (int i = 0; i < count; ++i) {
        QKeyEvent press(QEvent::KeyPress, keyCode, mods);
        QApplication::sendEvent(target, &press);
        QKeyEvent release(QEvent::KeyRelease, keyCode, mods);
        QApplication::sendEvent(target, &release);
    }
    return {};
}

QVariantMap InputSynth::typeText(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolveOrNull(params.value(QStringLiteral("handle")).toString());
    QWidget *target = qobject_cast<QWidget *>(object);
    if (!target)
        target = QApplication::focusWidget();
    if (!target)
        throw CommandError(ErrorCode::NotActionable, QStringLiteral("no target for text input"));

    const QString text = params.value(QStringLiteral("text")).toString();
    for (const QChar &ch : text) {
        QKeyEvent press(QEvent::KeyPress, 0, Qt::NoModifier, QString(ch));
        QApplication::sendEvent(target, &press);
        QKeyEvent release(QEvent::KeyRelease, 0, Qt::NoModifier, QString(ch));
        QApplication::sendEvent(target, &release);
    }
    return {};
}

QVariantMap InputSynth::setText(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolve(params.value(QStringLiteral("handle")).toString());
    const QString text = params.value(QStringLiteral("text")).toString();

    // Fast path used by Locator.fill(): set the property directly so the change signals fire once
    // rather than once per character. Falls back to nothing if the property is not writable, which
    // surfaces as a clear protocol error rather than a silent no-op.
    static const char *keys[] = {"text", "plainText", "currentText"};
    for (const char *key : keys) {
        if (object->property(key).isValid()) {
            if (object->setProperty(key, text))
                return {};
        }
    }
    throw CommandError(ErrorCode::Unsupported,
                       QStringLiteral("%1 has no writable text property")
                           .arg(QString::fromUtf8(object->metaObject()->className())));
}

QVariantMap InputSynth::press(ObjectRegistry &registry, const QVariantMap &params)
{
    int keyCode = 0;
    Qt::KeyboardModifiers mods;
    parseChord(params.value(QStringLiteral("key")).toString(), &keyCode, &mods);
    QWidget *target = keyTarget(registry, params);
    QKeyEvent event(QEvent::KeyPress, keyCode, mods);
    QApplication::sendEvent(target, &event);
    return {};
}

QVariantMap InputSynth::release(ObjectRegistry &registry, const QVariantMap &params)
{
    int keyCode = 0;
    Qt::KeyboardModifiers mods;
    parseChord(params.value(QStringLiteral("key")).toString(), &keyCode, &mods);
    QWidget *target = keyTarget(registry, params);
    QKeyEvent event(QEvent::KeyRelease, keyCode, mods);
    QApplication::sendEvent(target, &event);
    return {};
}

QVariantMap InputSynth::wheel(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);

    // One "step" is a notch of a real wheel, which Qt defines as 120 eighths of a degree. Positive
    // dy scrolls down, matching how a caller thinks about it, so the angle delta is negated.
    const int dx = params.value(QStringLiteral("dx"), 0).toInt();
    const int dy = params.value(QStringLiteral("dy"), 0).toInt();
    const QPoint angle(dx * -120, dy * -120);
    const QPoint pixels(dx * -20, dy * -20);

    // The viewport is the widget that actually scrolls; sending to the view itself is ignored by
    // QAbstractScrollArea.
    QWidget *target = widget;
    if (auto *area = qobject_cast<QAbstractScrollArea *>(widget)) {
        target = area->viewport();
        point = area->viewport()->rect().center();
    }
    const QPointF local(point);
    const QPointF global(target->mapToGlobal(point));

    QWheelEvent event(local, global, pixels, angle, Qt::NoButton,
                      parseModifiers(params.value(QStringLiteral("modifiers")).toList()),
                      Qt::NoScrollPhase, false);
    QApplication::sendEvent(target, &event);

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::drag(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint from;
    QWidget *source = targetWidget(registry, params, &from);

    const QString toHandle = params.value(QStringLiteral("to_handle")).toString();
    QObject *targetObject = registry.resolve(toHandle);
    auto *target = qobject_cast<QWidget *>(targetObject);
    if (!target) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("the drop target is not a widget"));
    }
    QPoint to;
    if (!WidgetBackend::interactionPointFor(target, toHandle, &to)) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("cannot compute a point on the drop target"));
    }

    const int steps = qMax(1, params.value(QStringLiteral("steps"), 10).toInt());
    const QPoint globalFrom = source->mapToGlobal(from);
    const QPoint globalTo = target->mapToGlobal(to);

    QMouseEvent press(QEvent::MouseButtonPress, from, globalFrom,
                      Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
    QApplication::sendEvent(source, &press);

    // Intermediate moves are what make this a drag rather than a teleport: widgets commonly wait
    // for QApplication::startDragDistance before they treat the gesture as one at all.
    for (int i = 1; i <= steps; ++i) {
        const QPoint global = globalFrom + (globalTo - globalFrom) * i / steps;
        QWidget *under = QApplication::widgetAt(global);
        QWidget *receiver = under ? under : source;
        const QPoint local = receiver->mapFromGlobal(global);
        QMouseEvent move(QEvent::MouseMove, local, global,
                         Qt::NoButton, Qt::LeftButton, Qt::NoModifier);
        QApplication::sendEvent(receiver, &move);
    }

    QMouseEvent release(QEvent::MouseButtonRelease, to, globalTo,
                        Qt::LeftButton, Qt::NoButton, Qt::NoModifier);
    QApplication::sendEvent(target, &release);

    QVariantMap out;
    out.insert(QStringLiteral("from"), QVariantList{globalFrom.x(), globalFrom.y()});
    out.insert(QStringLiteral("to"), QVariantList{globalTo.x(), globalTo.y()});
    return out;
}

} // namespace liberaqt
