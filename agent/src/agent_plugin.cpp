#include "agent_plugin.h"
#include "agent.h"

#include <QCoreApplication>
#include <QLoggingCategory>
#include <QTimer>

Q_LOGGING_CATEGORY(lcAgent, "qtdriver.agent")

namespace qtdriver {

AgentPlugin::AgentPlugin(QObject *parent)
    : QGenericPlugin(parent)
{
    // Refuse to do anything unless the launcher explicitly asked for automation. This means a
    // binary that accidentally ships with the plugin present is inert in production.
    if (qEnvironmentVariableIsEmpty("QTDRIVER_TOKEN")) {
        qCDebug(lcAgent) << "QTDRIVER_TOKEN not set; agent stays dormant";
        return;
    }

    // We are constructed from inside the QGuiApplication constructor: too early to create widgets
    // or bind sockets safely. Defer to the first event loop iteration.
    QTimer::singleShot(0, qApp, [] { Agent::instance().start(); });

    qCWarning(lcAgent) << "qtdriver agent armed -- this process is remotely automatable. "
                          "Never ship this plugin in a production build.";
}

QObject *AgentPlugin::create(const QString &, const QString &)
{
    return nullptr; // side-effect-only plugin
}

} // namespace qtdriver
