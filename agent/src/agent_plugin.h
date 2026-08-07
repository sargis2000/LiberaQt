#pragma once

#include <QObject>
#include <QGenericPlugin>

namespace liberaqt {

// Entry point. Qt instantiates this during QGuiApplication construction for every plugin named in
// QT_QPA_GENERIC_PLUGINS. We return nullptr from create() -- this is a pure side-effect plugin --
// and defer real work to a zero-delay timer so the event loop is running before we bind a socket.
class AgentPlugin : public QGenericPlugin
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QGenericPluginFactoryInterface_iid FILE "liberaqt.json")

public:
    explicit AgentPlugin(QObject *parent = nullptr);
    QObject *create(const QString &name, const QString &spec) override;
};

} // namespace liberaqt
