#pragma once

#include <QObject>
#include <QVariantMap>

class QTcpServer;
class QTcpSocket;

namespace qtdriver {

class Dispatcher;

// Newline-delimited JSON over a loopback TCP socket. One client at a time.
//
// The socket lives on the GUI thread in this skeleton for simplicity. Milestone 1 moves it to a
// worker thread and marshals every handler back with a queued invocation, so that a blocked GUI
// thread produces a clean client-side timeout instead of a hang.
class Server : public QObject
{
    Q_OBJECT
public:
    explicit Server(Dispatcher &dispatcher, QObject *parent = nullptr);
    ~Server() override;

    bool listen(const QString &address, quint16 port, const QString &token);
    void close();

    quint16 port() const;
    QString errorString() const;

    void sendEvent(const QString &name, const QVariantMap &data);

private:
    void onNewConnection();
    void onReadyRead();
    void sendHello();
    void writeMessage(const QVariantMap &message);
    void handleLine(const QByteArray &line);

    Dispatcher &m_dispatcher;
    QTcpServer *m_server = nullptr;
    QTcpSocket *m_client = nullptr;
    QByteArray m_buffer;
    QString m_token;
    bool m_authenticated = false;
};

} // namespace qtdriver
