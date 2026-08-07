// Optional in-app entry point.
//
// Teams that build their own application can link the agent directly instead of relying on plugin
// injection. This sidesteps every ABI and antivirus problem described in docs/INJECTION.md, and is
// the only option for statically-linked Qt builds.
//
//     #include <liberaqt/embed.h>
//     int main(int argc, char **argv) {
//         QApplication app(argc, argv);
//         liberaqt::startIfRequested();   // no-op unless LIBERAQT_TOKEN is set
//         ...
//     }
#pragma once

#include <QtGlobal>

namespace liberaqt {

// Starts the agent if LIBERAQT_TOKEN is present in the environment; otherwise does nothing.
// Safe to call unconditionally, including in production builds -- but prefer not to ship it.
Q_DECL_EXPORT void startIfRequested();

// Starts the agent unconditionally on the given port with the given token. For tests that manage
// their own lifecycle.
Q_DECL_EXPORT void start(quint16 port, const char *token);

} // namespace liberaqt
