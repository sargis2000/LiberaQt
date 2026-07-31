#include "agent.h"

#include "dispatcher.h"
#include "idle_tracker.h"
#include "object_registry.h"
#include "recorder.h"
#include "server.h"

#include <QCoreApplication>
#include <QFile>
#include <QGuiApplication>
#include <QLoggingCategory>
#include <QWindow>

Q_DECLARE_LOGGING_CATEGORY(lcAgent)

namespace qtdriver {

Agent &Agent::instance()
{
    static Agent agent;
    return agent;
}

Agent::Agent()
    : m_registry(std::make_unique<ObjectRegistry>())
    , m_dispatcher(std::make_unique<Dispatcher>(*m_registry))
    , m_server(std::make_unique<Server>(*m_dispatcher))
    , m_recorder(std::make_unique<Recorder>(*m_registry))
    , m_idle(std::make_unique<IdleTracker>())
{
}

Agent::~Agent() = default;

void Agent::start()
{
    if (m_started)
        return;
    m_started = true;

    const QByteArray token = qgetenv("QTDRIVER_TOKEN");
    const quint16 requested = static_cast<quint16>(qgetenv("QTDRIVER_PORT").toUShort());

    if (!m_server->listen(QStringLiteral("127.0.0.1"), requested, QString::fromUtf8(token))) {
        qCCritical(lcAgent) << "failed to listen:" << m_server->errorString();
        return;
    }

    publishPort(m_server->port());
    installWindowWatcher();

    if (!qEnvironmentVariableIsEmpty("QTDRIVER_RECORD"))
        m_recorder->start(QStringLiteral("semantic"));

    qCInfo(lcAgent) << "qtdriver agent listening on 127.0.0.1:" << m_server->port();
}

void Agent::stop()
{
    m_server->close();
    m_started = false;
}

// The launcher polls this file rather than parsing stdout, because many Qt apps are built as
// GUI subsystem binaries on Windows and have no usable stdout at all.
void Agent::publishPort(quint16 port)
{
    const QByteArray path = qgetenv("QTDRIVER_PORT_FILE");
    if (path.isEmpty())
        return;
    QFile file(QString::fromLocal8Bit(path));
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.write(QByteArray::number(port));
        file.close();
    } else {
        qCWarning(lcAgent) << "cannot write port file" << path;
    }
}

void Agent::installWindowWatcher()
{
    auto *gui = qobject_cast<QGuiApplication *>(QCoreApplication::instance());
    if (!gui)
        return;

    // TODO(m0): also watch QWidget top-levels that have no QWindow yet (not yet shown).
    QObject::connect(gui, &QGuiApplication::focusWindowChanged, this, [this](QWindow *w) {
        if (!w)
            return;
        QVariantMap data;
        data.insert(QStringLiteral("handle"), m_registry->handleFor(w));
        data.insert(QStringLiteral("title"), w->title());
        emitEvent(QStringLiteral("window.opened"), data);
    });

    QObject::connect(gui, &QCoreApplication::aboutToQuit, this, [this] {
        emitEvent(QStringLiteral("app.about_to_quit"), {});
        stop();
    });
}

void Agent::emitEvent(const QString &name, const QVariantMap &data)
{
    m_server->sendEvent(name, data);
}

} // namespace qtdriver
