#pragma once

#include <QVariantMap>

namespace liberaqt {

class ObjectRegistry;

// Synthesises input events.
//
// Two delivery strategies, chosen per command:
//   1. QWindowSystemInterface -- events enter at the platform layer, so they traverse the same
//      code path as real user input (grabs, hover state, focus changes, event filters). This is
//      the default: it is the only way to get behaviour that matches what a user sees.
//   2. QCoreApplication::sendEvent to a specific widget -- used when the target is obscured or
//      off-screen and the test explicitly asked for it. Faster, less realistic.
class InputSynth
{
public:
    static QVariantMap click(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap hover(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap key(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap typeText(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap setText(ObjectRegistry &registry, const QVariantMap &params);

    // Held keys, for shortcuts and modifier-dependent behaviour. Unlike key(), these do not pair
    // a press with a release, so the caller is responsible for letting go again.
    static QVariantMap press(ObjectRegistry &registry, const QVariantMap &params);
    static QVariantMap release(ObjectRegistry &registry, const QVariantMap &params);

    static QVariantMap wheel(ObjectRegistry &registry, const QVariantMap &params);

    // Press on the source, move across in steps, release on the target. The intermediate moves
    // matter: many widgets only begin a drag once the pointer has travelled far enough.
    static QVariantMap drag(ObjectRegistry &registry, const QVariantMap &params);

    // TODO(m2): IME composition
};

} // namespace liberaqt
