// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
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

namespace liberaqt {

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

    const QByteArray token = qgetenv("LIBERAQT_TOKEN");
    const quint16 requested = static_cast<quint16>(qgetenv("LIBERAQT_PORT").toUShort());

    if (!m_server->listen(QStringLiteral("127.0.0.1"), requested, QString::fromUtf8(token))) {
        qCCritical(lcAgent) << "failed to listen:" << m_server->errorString();
        return;
    }

    publishPort(m_server->port());
    installWindowWatcher();

    if (!qEnvironmentVariableIsEmpty("LIBERAQT_RECORD"))
        m_recorder->start(QStringLiteral("semantic"));

    qCInfo(lcAgent) << "liberaqt agent listening on 127.0.0.1:" << m_server->port();
}

void Agent::stop()
{
    m_server->close();
    m_started = false;
}

// The launcher polls this file rather than parsing stdout, because many Qt apps are built as
// GUI subsystem binaries on Windows and have no usable stdout at all.
void Agent::writePortFile(const QString &path, quint16 port)
{
    QFile file(path);
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.write(QByteArray::number(port));
        file.close();
    } else {
        qCWarning(lcAgent) << "cannot write port file" << path;
    }
}

void Agent::publishPort(quint16 port)
{
    // One file per process, named after the pid.
    //
    // A single shared path cannot survive an application that spawns Qt children of its own:
    // they inherit the injection environment, load the agent too, and each one overwrites what
    // the last wrote. Measured on Libero SoC, whose core configurator (a separate coreconfig.exe)
    // replaced the parent's port with its own within seconds of opening. The launcher only reads
    // the file once at startup, so it went unnoticed -- anything re-reading it would have been
    // talking to the wrong process.
    //
    // Keying on the pid also turns those children from an accident into something findable: the
    // directory is a list of every agent in the process tree.
    const QByteArray dir = qgetenv("LIBERAQT_PORT_DIR");
    if (!dir.isEmpty()) {
        writePortFile(QStringLiteral("%1/%2.port")
                          .arg(QString::fromLocal8Bit(dir))
                          .arg(QCoreApplication::applicationPid()),
                      port);
    }

    // Still honoured for anything that sets it deliberately (the embedded build, INJECTION.md
    // section 4). The launcher no longer does, precisely so that nothing inherits it.
    const QByteArray path = qgetenv("LIBERAQT_PORT_FILE");
    if (!path.isEmpty())
        writePortFile(QString::fromLocal8Bit(path), port);
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

} // namespace liberaqt
