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

    // TODO(m1): press/release, wheel, drag, IME composition
};

} // namespace liberaqt
