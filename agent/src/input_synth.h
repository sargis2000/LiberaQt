#pragma once

#include <QVariantMap>

namespace liberaqt {

class ObjectRegistry;

// Synthesises input events.
//
// Two delivery strategies, chosen per session or per command:
//
//   Native (the default) -- events are queued through QWindowSystemInterface, the same seam a
//     platform plugin pushes real input through. Qt then does everything it does for a real user:
//     hit-testing to find the receiver, hover and enter/leave tracking, the implicit mouse grab
//     between press and release, double-click detection, popup dismissal, focus changes, and
//     blocking input to a window a modal dialog has disabled. Widgets see spontaneous() events.
//
//   Synthetic -- QCoreApplication::sendEvent aimed at one widget. None of the above happens; the
//     widget's handler is simply called. Retained because it still reaches a target that is
//     scrolled out of view or covered, which a real click cannot.
//
// Everything here only *queues*: the platform event dispatcher drains the queue on the next pass
// through the event loop. Callers must therefore be asynchronous commands that resolve a turn
// later -- see the note in compat.h.
class InputSynth
{
public:
    enum class Mode { Native, Synthetic };

    // Session default, settable over the wire with session.set_options({"input_mode": ...}).
    // An individual command overrides it with a "mode" parameter.
    static Mode mode();
    static void setMode(Mode mode);
    static bool parseMode(const QString &name, Mode *out);

    static QVariantMap click(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap hover(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap key(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap typeText(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap setText(ObjectRegistry &registry, const QVariantMap &params);

    // Held keys and buttons, for shortcuts and modifier-dependent behaviour. Unlike key(), these
    // do not pair a press with a release, so the caller is responsible for letting go again.
    static QVariantMap press(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap release(ObjectRegistry &registry, const QVariantMap &params);

    static QVariantMap wheel(ObjectRegistry &registry, const QVariantMap &params);

    // Press on the source, move across in steps, release on the target. The intermediate moves
    // matter: many widgets only begin a drag once the pointer has travelled far enough.
    static QVariantMap drag(ObjectRegistry &registry, const QVariantMap &params);

    // TODO(m2): IME composition
};

} // namespace liberaqt
