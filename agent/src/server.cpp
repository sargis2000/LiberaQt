#include "server.h"

#include "dispatcher.h"

#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLoggingCategory>
#include <QPointer>
#include <QTcpServer>
#include <QTcpSocket>

Q_DECLARE_LOGGING_CATEGORY(lcAgent)

namespace liberaqt {

Server::Server(Dispatcher &dispatcher, QObject *parent)
    : QObject(parent)
    , m_dispatcher(dispatcher)
{
}

Server::~Server() = default;

bool Server::listen(const QString &address, quint16 port, const QString &token)
{
    m_token = token;
    m_server = new QTcpServer(this);
    connect(m_server, &QTcpServer::newConnection, this, &Server::onNewConnection);
    // Loopback only, always. Never bind 0.0.0.0: the agent is a remote-code-execution surface.
    return m_server->listen(QHostAddress(address), port);
}

void Server::close()
{
    if (m_client) {
        m_client->disconnectFromHost();
        m_client = nullptr;
    }
    if (m_server) {
        m_server->close();
    }
}

quint16 Server::port() const
{
    return m_server ? m_server->serverPort() : 0;
}

QString Server::errorString() const
{
    return m_server ? m_server->errorString() : QStringLiteral("no server");
}

void Server::onNewConnection()
{
    auto *socket = m_server->nextPendingConnection();
    if (m_client) {
        // Exactly one driver at a time; a second connection is almost always a mistake.
        socket->write("{\"type\":\"error\",\"message\":\"another client is already connected\"}\n");
        socket->disconnectFromHost();
        return;
    }
    m_client = socket;
    m_authenticated = false;
    m_buffer.clear();

    connect(m_client, &QTcpSocket::readyRead, this, &Server::onReadyRead);
    connect(m_client, &QTcpSocket::disconnected, this, [this] {
        m_client = nullptr;
        m_authenticated = false;
    });

    sendHello();
}

void Server::sendHello()
{
    QVariantMap hello;
    hello.insert(QStringLiteral("type"), QStringLiteral("hello"));
    hello.insert(QStringLiteral("protocol"), LIBERAQT_PROTOCOL);
    hello.insert(QStringLiteral("agent"), QString::fromUtf8(LIBERAQT_VERSION));
    hello.insert(QStringLiteral("qt"), QString::fromUtf8(qVersion()));
#if defined(Q_OS_WIN)
    hello.insert(QStringLiteral("platform"), QStringLiteral("windows"));
#elif defined(Q_OS_LINUX)
    hello.insert(QStringLiteral("platform"), QStringLiteral("linux"));
#else
    hello.insert(QStringLiteral("platform"), QStringLiteral("other"));
#endif
    hello.insert(QStringLiteral("pid"), QCoreApplication::applicationPid());

    QVariantMap app;
    app.insert(QStringLiteral("name"), QCoreApplication::applicationName());
    app.insert(QStringLiteral("widgets"), true);
#ifdef LIBERAQT_HAVE_QUICK
    app.insert(QStringLiteral("quick"), true);
#else
    app.insert(QStringLiteral("quick"), false);
#endif
    hello.insert(QStringLiteral("app"), app);

    writeMessage(hello);
}

void Server::onReadyRead()
{
    m_buffer += m_client->readAll();

    // A handler can let the application run -- and therefore re-enter this slot through a nested
    // event loop. Only the outermost frame may consume the buffer; a nested one appends what it
    // read and returns, leaving the outer loop to pick the new lines up.
    if (m_dispatching)
        return;
    m_dispatching = true;

    int newline;
    while ((newline = m_buffer.indexOf('\n')) >= 0) {
        const QByteArray line = m_buffer.left(newline);
        m_buffer.remove(0, newline + 1);
        if (!line.trimmed().isEmpty())
            handleLine(line);
    }

    m_dispatching = false;
}

void Server::handleLine(const QByteArray &line)
{
    QJsonParseError parseError{};
    const QJsonDocument doc = QJsonDocument::fromJson(line, &parseError);
    if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
        QVariantMap err;
        err.insert(QStringLiteral("type"), QStringLiteral("error"));
        err.insert(QStringLiteral("message"), parseError.errorString());
        writeMessage(err);
        return;
    }

    const QVariantMap message = doc.object().toVariantMap();

    if (message.value(QStringLiteral("type")).toString() == QLatin1String("auth")) {
        m_authenticated = (message.value(QStringLiteral("token")).toString() == m_token)
                          && (message.value(QStringLiteral("protocol")).toInt() == LIBERAQT_PROTOCOL);
        if (!m_authenticated) {
            qCWarning(lcAgent) << "rejecting client: bad token or protocol";
            QVariantMap err;
            err.insert(QStringLiteral("type"), QStringLiteral("error"));
            err.insert(QStringLiteral("message"), QStringLiteral("authentication failed"));
            writeMessage(err);
            m_client->disconnectFromHost();
        }
        return;
    }

    if (!m_authenticated) {
        QVariantMap err;
        err.insert(QStringLiteral("type"), QStringLiteral("error"));
        err.insert(QStringLiteral("message"), QStringLiteral("not authenticated"));
        writeMessage(err);
        return;
    }

    // The reply may be delivered long after handle() returns, so it is written through a guarded
    // pointer: by then the client may well have gone away.
    QPointer<Server> self(this);
    m_dispatcher.handle(message, [self](const QVariantMap &response) {
        if (self)
            self->writeMessage(response);
    });
}

void Server::writeMessage(const QVariantMap &message)
{
    if (!m_client)
        return;
    const QJsonDocument doc(QJsonObject::fromVariantMap(message));
    m_client->write(doc.toJson(QJsonDocument::Compact));
    m_client->write("\n");
    m_client->flush();
}

void Server::sendEvent(const QString &name, const QVariantMap &data)
{
    if (!m_client || !m_authenticated)
        return;
    QVariantMap message;
    message.insert(QStringLiteral("type"), QStringLiteral("event"));
    message.insert(QStringLiteral("event"), name);
    message.insert(QStringLiteral("data"), data);
    writeMessage(message);
}

} // namespace liberaqt
