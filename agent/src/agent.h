// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#pragma once

#include <QVariantMap>
#include <QObject>
#include <memory>

namespace liberaqt {

class Server;
class Dispatcher;
class ObjectRegistry;
class Recorder;
class IdleTracker;

// Process-wide singleton wiring the pieces together. Lives on the GUI thread.
class Agent : public QObject
{
    Q_OBJECT
public:
    static Agent &instance();

    void start();
    void stop();

    ObjectRegistry &registry() const { return *m_registry; }
    Dispatcher &dispatcher() const { return *m_dispatcher; }
    IdleTracker &idle() const { return *m_idle; }
    Recorder &recorder() const { return *m_recorder; }

    // Emitted to the client as protocol events.
    void emitEvent(const QString &name, const QVariantMap &data);

private:
    Agent();
    ~Agent() override;

    void publishPort(quint16 port);
    static void writePortFile(const QString &path, quint16 port);
    void installWindowWatcher();

    std::unique_ptr<ObjectRegistry> m_registry;
    std::unique_ptr<Dispatcher> m_dispatcher;
    std::unique_ptr<Server> m_server;
    std::unique_ptr<Recorder> m_recorder;
    std::unique_ptr<IdleTracker> m_idle;
    bool m_started = false;
};

} // namespace liberaqt
