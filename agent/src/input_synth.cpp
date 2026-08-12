#include "input_synth.h"

#include "compat.h"
#include "dispatcher.h"
#include "object_registry.h"
#include "widget_backend.h"

#include <QAbstractScrollArea>
#include <QApplication>
#include <QContextMenuEvent>
#include <QKeyEvent>
#include <QKeySequence>
#include <QMouseEvent>
#include <QPoint>
#include <QStringList>
#include <QWheelEvent>
#include <QWidget>
#include <QWindow>

namespace liberaqt {

namespace {

InputSynth::Mode g_mode = InputSynth::Mode::Native;

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

InputSynth::Mode modeFor(const QVariantMap &params)
{
    const QString name = params.value(QStringLiteral("mode")).toString();
    InputSynth::Mode mode = InputSynth::mode();
    if (!name.isEmpty() && !InputSynth::parseMode(name, &mode)) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("unknown input mode '%1'; expected 'native' or "
                                          "'synthetic'").arg(name));
    }
    return mode;
}

// A point to aim at, in every coordinate system the two delivery paths need.
struct Target
{
    QWidget *widget = nullptr; // what the caller named
    QPoint point;              // the interaction point, in that widget's coordinates
    QPoint global;             // the same point on the screen
    QWindow *window = nullptr; // the platform window it belongs to, if the widget is mapped
};

Target locate(QWidget *widget, QPoint point)
{
    Target target;
    target.widget = widget;
    target.point = point;
    target.global = widget->mapToGlobal(point);
    target.window = widget->window()->windowHandle();
    return target;
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

// Native input enters through a platform window, so a widget whose window has not been created
// yet cannot receive any. Saying so beats queuing events into nothing and reporting success.
const Target &requireWindow(const Target &target)
{
    if (!target.window) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("%1 has no platform window yet, so it cannot receive "
                                          "input; is it shown?")
                               .arg(QString::fromUtf8(target.widget->metaObject()->className())));
    }
    return target;
}

// A real click on a window that is not in front activates it, and the platform does that before
// the press arrives. Injected input has to do the same or the application stays permanently "in
// the background" however hard the test clicks: QApplication::activeWindow() stays null, and
// window-context shortcuts -- which is nearly all of them -- quietly match nothing.
//
// Not past a modal dialog, though. A user cannot bring the window behind one to the front either;
// left inactive, the event is dropped by Qt's own modal check, which is the right answer.
void ensureActive(QWidget *widget, QWindow *window)
{
    if (window->isActive() || window->type() == Qt::Popup || window->type() == Qt::ToolTip)
        return;
    if (QWidget *modal = QApplication::activeModalWidget()) {
        if (modal != widget->window() && !modal->isAncestorOf(widget))
            return;
    }
    compat::postWindowActivated(window);
}

void nativeMouse(const Target &target, QEvent::Type type, Qt::MouseButtons buttons,
                 Qt::MouseButton button, Qt::KeyboardModifiers mods)
{
    compat::postMouse(target.window, QPointF(target.window->mapFromGlobal(target.global)),
                      QPointF(target.global), buttons, button, type, mods);
}

// The widget Qt would hand a mouse event to at this point. childAt() walks the widget hierarchy
// itself, unlike QApplication::widgetAt(), which asks the window system and so answers nothing
// when another application's window happens to be in front.
QWidget *widgetUnder(const Target &target)
{
    QWidget *top = target.widget->window();
    QWidget *hit = top->childAt(top->mapFromGlobal(target.global));
    return hit ? hit : top;
}

// Where a mouse event has to be delivered on the synthetic path, and the point in that widget's
// coordinates. A QAbstractScrollArea does not handle mouse events itself: Qt routes them through
// the viewport, and an item view's press handling hangs off that. An event sent to the frame is
// silently ignored -- the call reports success and nothing happens, which is worse than an error.
// The native path needs none of this, because Qt does the same routing for us.
QWidget *mouseReceiver(QWidget *widget, QPoint *point)
{
    if (auto *area = qobject_cast<QAbstractScrollArea *>(widget)) {
        QWidget *viewport = area->viewport();
        *point = viewport->mapFrom(widget, *point);
        return viewport;
    }
    return widget;
}

void parseChord(const QString &spec, int *keyCode, Qt::KeyboardModifiers *mods)
{
    const QKeySequence sequence(spec);
    if (sequence.isEmpty()) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("cannot parse key sequence '%1'").arg(spec));
    }
    compat::firstChord(sequence, keyCode, mods);
}

// Where a keystroke goes: the window it enters through, and the widget inside it that will
// receive it. Both, because the two delivery paths need different halves.
struct KeyTarget
{
    QWidget *widget = nullptr;
    QWindow *window = nullptr;
};

// The top-level a keystroke belongs to when the caller named no widget, in the order Qt itself
// consults: an open popup grabs the keyboard, then a modal dialog, then the active window.
//
// Deliberately not QApplication::focusWidget(). That is null whenever the application is not the
// foreground one -- which is the normal state of an application being driven by a test, and
// especially of one of several -- so keying off it makes every keystroke fail with "nothing is
// focused" for a reason that has nothing to do with the application.
QWidget *implicitKeyWindow()
{
    if (QWidget *popup = QApplication::activePopupWidget())
        return popup;
    if (QWidget *modal = QApplication::activeModalWidget())
        return modal;
    if (QWidget *active = QApplication::activeWindow())
        return active;

    QWidget *only = nullptr;
    QStringList candidates;
    for (QWidget *top : QApplication::topLevelWidgets()) {
        if (!top->isVisible() || !top->windowHandle())
            continue;
        only = top;
        candidates << (top->windowTitle().isEmpty()
                           ? QString::fromUtf8(top->metaObject()->className())
                           : top->windowTitle());
    }
    if (candidates.size() == 1)
        return only;
    throw CommandError(ErrorCode::NotActionable,
                       candidates.isEmpty()
                           ? QStringLiteral("the application has no window to type into")
                           : QStringLiteral("no window is active, and there are %1 to choose "
                                            "from: %2. Name a widget, or activate a window first.")
                                 .arg(candidates.size())
                                 .arg(candidates.join(QLatin1String(", "))));
}

// A named target that does not already hold focus is focused first, or the text lands wherever
// the user last clicked. A widget that declines focus is left alone -- a user could not type into
// it either, and the keys go to whatever the window really has focused.
KeyTarget keyTarget(ObjectRegistry &registry, const QVariantMap &params)
{
    KeyTarget target;
    QObject *object = registry.resolveOrNull(params.value(QStringLiteral("handle")).toString());
    if (auto *named = qobject_cast<QWidget *>(object)) {
        if (!named->hasFocus() && named->focusPolicy() != Qt::NoFocus)
            named->setFocus(Qt::MouseFocusReason);
        target.widget = named;
    } else {
        QWidget *top = implicitKeyWindow();
        // focusWidget() is the window's own focus, tracked whether or not the window is active.
        target.widget = top->focusWidget() ? top->focusWidget() : top;
    }

    target.window = target.widget->window()->windowHandle();
    if (!target.window) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("the target's window is not mapped yet"));
    }
    return target;
}

struct ModifierKey
{
    Qt::KeyboardModifier flag;
    int key;
};

// Order matters only in that the release mirrors the press, as a hand does.
const ModifierKey ModifierKeys[] = {
    {Qt::ControlModifier, Qt::Key_Control},
    {Qt::AltModifier, Qt::Key_Alt},
    {Qt::ShiftModifier, Qt::Key_Shift},
    {Qt::MetaModifier, Qt::Key_Meta},
};
const int ModifierKeyCount = int(sizeof(ModifierKeys) / sizeof(ModifierKeys[0]));

// One chord, pressed the way a hand presses it: modifiers down, key down, key up, modifiers up.
// Applications do watch the modifier keys themselves -- they swap cursors, switch rubber-band
// modes and underline menu accelerators -- so delivering only the final combination skips
// everything a user would have triggered on the way to it.
void nativeChord(QWindow *window, int keyCode, Qt::KeyboardModifiers mods, const QString &text)
{
    Qt::KeyboardModifiers held = Qt::NoModifier;
    for (const ModifierKey &modifier : ModifierKeys) {
        if (mods.testFlag(modifier.flag)) {
            held |= modifier.flag;
            compat::postKey(window, QEvent::KeyPress, modifier.key, held, QString());
        }
    }
    compat::postKey(window, QEvent::KeyPress, keyCode, mods, text);
    compat::postKey(window, QEvent::KeyRelease, keyCode, mods, text);
    for (int i = ModifierKeyCount - 1; i >= 0; --i) {
        if (mods.testFlag(ModifierKeys[i].flag)) {
            held &= ~ModifierKeys[i].flag;
            compat::postKey(window, QEvent::KeyRelease, ModifierKeys[i].key, held, QString());
        }
    }
}

// A keystroke carries a key code as well as its text, and widgets read whichever they need: a
// QLineEdit inserts event->text(), while shortcuts, item-view type-ahead and every keyPressEvent
// override switch on event->key(). Sending text with key 0 types into line edits and is invisible
// to all the rest. Qt::Key values coincide with upper-case ASCII across the printable range.
int keyForChar(QChar ch)
{
    switch (ch.unicode()) {
    case '\n':
    case '\r':
        return Qt::Key_Return;
    case '\t':
        return Qt::Key_Tab;
    case '\b':
        return Qt::Key_Backspace;
    default:
        return ch.toUpper().unicode();
    }
}

} // namespace

InputSynth::Mode InputSynth::mode()
{
    return g_mode;
}

void InputSynth::setMode(Mode mode)
{
    g_mode = mode;
}

bool InputSynth::parseMode(const QString &name, Mode *out)
{
    if (name == QLatin1String("native")) {
        *out = Mode::Native;
        return true;
    }
    if (name == QLatin1String("synthetic")) {
        *out = Mode::Synthetic;
        return true;
    }
    return false;
}

QVariantMap InputSynth::click(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);
    const Target target = locate(widget, point);

    const Qt::MouseButton button = parseButton(params.value(QStringLiteral("button")).toString());
    const Qt::KeyboardModifiers mods =
        parseModifiers(params.value(QStringLiteral("modifiers")).toList());
    const int count = qMax(1, params.value(QStringLiteral("count"), 1).toInt());

    if (modeFor(params) == Mode::Native) {
        requireWindow(target);
        ensureActive(widget, target.window);
        // The move comes first because real input always does. Hover highlighting, tooltips, menu
        // tracking and the enter/leave pair all key off the pointer arriving before the button
        // goes down, and a widget that only reacts once hovered ignores a click without it.
        nativeMouse(target, QEvent::MouseMove, Qt::NoButton, Qt::NoButton, mods);
        for (int i = 0; i < count; ++i) {
            nativeMouse(target, QEvent::MouseButtonPress, button, button, mods);
            nativeMouse(target, QEvent::MouseButtonRelease, Qt::NoButton, button, mods);
        }
        // No MouseButtonDblClick is queued for count == 2. Qt derives one itself from two presses
        // that arrive within the double-click interval, exactly as it does for a user; sending it
        // as well would deliver the second click twice.
    } else {
        QPoint local = point;
        QWidget *receiver = mouseReceiver(widget, &local);
        for (int i = 0; i < count; ++i) {
            const QEvent::Type pressType = (i == 1) ? QEvent::MouseButtonDblClick
                                                    : QEvent::MouseButtonPress;
            QMouseEvent press(pressType, local, target.global, button, button, mods);
            QApplication::sendEvent(receiver, &press);
            QMouseEvent release(QEvent::MouseButtonRelease, local, target.global, button,
                                Qt::NoButton, mods);
            QApplication::sendEvent(receiver, &release);
        }
    }

    // A context menu is not derived from the mouse event even on the native path: on a real
    // right-click the *platform* sends a separate QContextMenuEvent, and no platform is involved
    // here. Without one, a right-click selects the item and nothing more. Posted rather than sent,
    // because showing a context menu runs a nested event loop.
    if (button == Qt::RightButton) {
        QWidget *receiver = widgetUnder(target);
        QApplication::postEvent(receiver,
                                new QContextMenuEvent(QContextMenuEvent::Mouse,
                                                      receiver->mapFromGlobal(target.global),
                                                      target.global, mods));
    }

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::hover(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);
    const Target target = locate(widget, point);

    if (modeFor(params) == Mode::Native) {
        requireWindow(target);
        nativeMouse(target, QEvent::MouseMove, Qt::NoButton, Qt::NoButton, Qt::NoModifier);
    } else {
        QPoint local = point;
        QWidget *receiver = mouseReceiver(widget, &local);
        QMouseEvent move(QEvent::MouseMove, local, target.global,
                         Qt::NoButton, Qt::NoButton, Qt::NoModifier);
        QApplication::sendEvent(receiver, &move);
    }

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::key(ObjectRegistry &registry, const QVariantMap &params)
{
    int keyCode = 0;
    Qt::KeyboardModifiers mods;
    parseChord(params.value(QStringLiteral("key")).toString(), &keyCode, &mods);

    // TODO(m1): multi-chord sequences. This handles the single-chord case only.
    const KeyTarget target = keyTarget(registry, params);
    const int count = qMax(1, params.value(QStringLiteral("count"), 1).toInt());

    if (modeFor(params) == Mode::Native) {
        // Typing implies the application is the one in front; a window-context shortcut such as
        // Ctrl+F matches nothing at all while QApplication::activeWindow() is null.
        ensureActive(target.widget, target.window);
        for (int i = 0; i < count; ++i)
            nativeChord(target.window, keyCode, mods, QString());
    } else {
        for (int i = 0; i < count; ++i) {
            QKeyEvent press(QEvent::KeyPress, keyCode, mods);
            QApplication::sendEvent(target.widget, &press);
            QKeyEvent release(QEvent::KeyRelease, keyCode, mods);
            QApplication::sendEvent(target.widget, &release);
        }
    }
    return {};
}

QVariantMap InputSynth::typeText(ObjectRegistry &registry, const QVariantMap &params)
{
    const KeyTarget target = keyTarget(registry, params);
    const QString text = params.value(QStringLiteral("text")).toString();

    if (modeFor(params) == Mode::Native) {
        ensureActive(target.widget, target.window);
        for (const QChar &ch : text) {
            // Shift for an upper-case letter, because that is the keystroke that produced it.
            const Qt::KeyboardModifiers mods =
                ch.isUpper() ? Qt::ShiftModifier : Qt::KeyboardModifiers(Qt::NoModifier);
            compat::postKey(target.window, QEvent::KeyPress, keyForChar(ch), mods, QString(ch));
            compat::postKey(target.window, QEvent::KeyRelease, keyForChar(ch), mods, QString(ch));
        }
    } else {
        for (const QChar &ch : text) {
            QKeyEvent press(QEvent::KeyPress, 0, Qt::NoModifier, QString(ch));
            QApplication::sendEvent(target.widget, &press);
            QKeyEvent release(QEvent::KeyRelease, 0, Qt::NoModifier, QString(ch));
            QApplication::sendEvent(target.widget, &release);
        }
    }
    return {};
}

QVariantMap InputSynth::setText(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolve(params.value(QStringLiteral("handle")).toString());
    const QString text = params.value(QStringLiteral("text")).toString();

    // Fast path used by Locator.fill(): set the property directly so the change signals fire once
    // rather than once per character. This is the one command that is deliberately *not* user
    // input -- it reaches fields a user could not, which is why read-only ones are refused here
    // rather than written to silently.
    const QVariant readOnly = object->property("readOnly");
    if (readOnly.isValid() && readOnly.toBool()) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("%1 is read-only; a user could not type into it")
                               .arg(QString::fromUtf8(object->metaObject()->className())));
    }

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

namespace {

// press/release serve both halves of the API: Keyboard.down/up send "key", Mouse.down/up send
// "button". One command, because to the caller they are the same idea -- hold something down --
// and splitting them would mean two names for one concept on the wire.
QVariantMap pressOrRelease(ObjectRegistry &registry, const QVariantMap &params, bool down)
{
    const InputSynth::Mode mode = modeFor(params);
    const QVariant key = params.value(QStringLiteral("key"));
    if (key.isValid() && !key.toString().isEmpty()) {
        int keyCode = 0;
        Qt::KeyboardModifiers mods;
        parseChord(key.toString(), &keyCode, &mods);
        const KeyTarget target = keyTarget(registry, params);
        const QEvent::Type type = down ? QEvent::KeyPress : QEvent::KeyRelease;
        if (mode == InputSynth::Mode::Native) {
            if (down)
                ensureActive(target.widget, target.window);
            compat::postKey(target.window, type, keyCode, mods, QString());
        } else {
            QKeyEvent event(type, keyCode, mods);
            QApplication::sendEvent(target.widget, &event);
        }
        return {};
    }

    QPoint point;
    QWidget *widget = targetWidget(registry, params, &point);
    const Target target = locate(widget, point);
    const Qt::MouseButton button = parseButton(params.value(QStringLiteral("button")).toString());
    const Qt::KeyboardModifiers mods =
        parseModifiers(params.value(QStringLiteral("modifiers")).toList());
    const QEvent::Type type = down ? QEvent::MouseButtonPress : QEvent::MouseButtonRelease;
    const Qt::MouseButtons state = down ? Qt::MouseButtons(button) : Qt::MouseButtons(Qt::NoButton);

    if (mode == InputSynth::Mode::Native) {
        requireWindow(target);
        if (down) {
            ensureActive(widget, target.window);
            nativeMouse(target, QEvent::MouseMove, Qt::NoButton, Qt::NoButton, mods);
        }
        nativeMouse(target, type, state, button, mods);
    } else {
        QPoint local = point;
        QWidget *receiver = mouseReceiver(widget, &local);
        QMouseEvent event(type, local, target.global, button, state, mods);
        QApplication::sendEvent(receiver, &event);
    }

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

} // namespace

QVariantMap InputSynth::press(ObjectRegistry &registry, const QVariantMap &params)
{
    return pressOrRelease(registry, params, true);
}

QVariantMap InputSynth::release(ObjectRegistry &registry, const QVariantMap &params)
{
    return pressOrRelease(registry, params, false);
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
    const Qt::KeyboardModifiers mods =
        parseModifiers(params.value(QStringLiteral("modifiers")).toList());

    if (modeFor(params) == Mode::Native) {
        const Target target = requireWindow(locate(widget, point));
        compat::postWheel(target.window, QPointF(target.window->mapFromGlobal(target.global)),
                          QPointF(target.global), pixels, angle, mods);
    } else {
        // The viewport is the widget that actually scrolls; sending to the view itself is ignored
        // by QAbstractScrollArea.
        QWidget *receiver = widget;
        if (auto *area = qobject_cast<QAbstractScrollArea *>(widget)) {
            receiver = area->viewport();
            point = receiver->rect().center();
        }
        QWheelEvent event(QPointF(point), QPointF(receiver->mapToGlobal(point)), pixels, angle,
                          Qt::NoButton, mods, Qt::NoScrollPhase, false);
        QApplication::sendEvent(receiver, &event);
    }

    QVariantMap out;
    out.insert(QStringLiteral("pos"), QVariantList{point.x(), point.y()});
    return out;
}

QVariantMap InputSynth::drag(ObjectRegistry &registry, const QVariantMap &params)
{
    QPoint from;
    QWidget *source = targetWidget(registry, params, &from);

    // Two callers, two shapes: Locator.drag_to names another object, while Mouse.drag gives raw
    // coordinates inside the window it is bound to.
    const QVariant fromPos = params.value(QStringLiteral("from_pos"));
    if (fromPos.isValid() && !fromPos.isNull()) {
        const QVariantList xy = fromPos.toList();
        from = QPoint(xy.value(0).toInt(), xy.value(1).toInt());
    }

    QWidget *target = source;
    QPoint to;
    const QVariant toPos = params.value(QStringLiteral("to_pos"));
    if (toPos.isValid() && !toPos.isNull()) {
        const QVariantList xy = toPos.toList();
        to = QPoint(xy.value(0).toInt(), xy.value(1).toInt());
    } else {
        const QString toHandle = params.value(QStringLiteral("to_handle")).toString();
        if (toHandle.isEmpty()) {
            throw CommandError(ErrorCode::InvalidParams,
                               QStringLiteral("give either 'to_handle' or 'to_pos'"));
        }
        target = qobject_cast<QWidget *>(registry.resolve(toHandle));
        if (!target) {
            throw CommandError(ErrorCode::Unsupported,
                               QStringLiteral("the drop target is not a widget"));
        }
        if (!WidgetBackend::interactionPointFor(target, toHandle, &to)) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("cannot compute a point on the drop target"));
        }
    }

    const int steps = qMax(1, params.value(QStringLiteral("steps"), 10).toInt());
    const QPoint globalFrom = source->mapToGlobal(from);
    const QPoint globalTo = target->mapToGlobal(to);

    if (modeFor(params) == Mode::Native) {
        const Target start = requireWindow(locate(source, from));
        QWindow *window = start.window;
        ensureActive(source, window);
        nativeMouse(start, QEvent::MouseMove, Qt::NoButton, Qt::NoButton, Qt::NoModifier);
        nativeMouse(start, QEvent::MouseButtonPress, Qt::LeftButton, Qt::LeftButton,
                    Qt::NoModifier);
        // Every later event goes through the *source's* window even when the pointer travels over
        // another one. That is not a shortcut: Qt holds an implicit grab from press to release, so
        // this is where a real drag's events would go too.
        for (int i = 1; i <= steps; ++i) {
            const QPoint global = globalFrom + (globalTo - globalFrom) * i / steps;
            compat::postMouse(window, QPointF(window->mapFromGlobal(global)), QPointF(global),
                              Qt::LeftButton, Qt::NoButton, QEvent::MouseMove, Qt::NoModifier);
        }
        compat::postMouse(window, QPointF(window->mapFromGlobal(globalTo)), QPointF(globalTo),
                          Qt::NoButton, Qt::LeftButton, QEvent::MouseButtonRelease,
                          Qt::NoModifier);
    } else {
        QMouseEvent press(QEvent::MouseButtonPress, from, globalFrom,
                          Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
        QApplication::sendEvent(source, &press);

        // Intermediate moves are what make this a drag rather than a teleport: widgets commonly
        // wait for QApplication::startDragDistance before they treat the gesture as one at all.
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
    }

    QVariantMap out;
    out.insert(QStringLiteral("from"), QVariantList{globalFrom.x(), globalFrom.y()});
    out.insert(QStringLiteral("to"), QVariantList{globalTo.x(), globalTo.y()});
    return out;
}

} // namespace liberaqt
