#pragma once

#include <QMap>
#include <QObject>
#include <QVariantMap>
#include <functional>

namespace liberaqt {

class ObjectRegistry;

// Protocol error codes -- keep in sync with docs/PROTOCOL.md section 2 and the Python
// ERROR_CODE_MAP in errors.py.
namespace ErrorCode {
extern const char *NotFound;
extern const char *Ambiguous;
extern const char *Stale;
extern const char *NotActionable;
extern const char *Unsupported;
extern const char *InvalidParams;
extern const char *Internal;
extern const char *Timeout;
}

class CommandError
{
public:
    CommandError(const char *code, QString message, QVariantMap data = {})
        : code(QString::fromLatin1(code)), message(std::move(message)), data(std::move(data)) {}
    QString code;
    QString message;
    QVariantMap data;
};

class Dispatcher
{
public:
    using Handler = std::function<QVariant(const QVariantMap &params)>;

    // Hands a finished response envelope back to the transport. Called synchronously for ordinary
    // commands, and later from the event loop for asynchronous ones.
    using Reply = std::function<void(const QVariantMap &response)>;

    // An asynchronous command finishes by calling exactly one of resolve/reject, possibly long
    // after the handler itself has returned. Commands that have to let the application run --
    // waiting for the UI to settle, waiting for a signal -- must be asynchronous: blocking inside
    // a handler strands the reply if the application enters a nested event loop of its own.
    using Resolver = std::function<void(const QVariant &result)>;
    using Rejecter = std::function<void(const CommandError &error)>;
    using AsyncHandler =
        std::function<void(const QVariantMap &params, Resolver resolve, Rejecter reject)>;

    explicit Dispatcher(ObjectRegistry &registry);

    // Builds the protocol response for a request and passes it to `reply`.
    void handle(const QVariantMap &request, Reply reply);

    void registerCommand(const QString &name, Handler handler);
    void registerAsyncCommand(const QString &name, AsyncHandler handler);

private:
    void registerBuiltins();

    // An input command, whose handler queues events rather than delivering them. Registered
    // asynchronously so the reply waits until the queue has been drained -- see the comment on
    // the definition for why replying immediately is a bug and not an optimisation.
    void registerInputCommand(const QString &name, Handler handler);

    ObjectRegistry &m_registry;
    QMap<QString, Handler> m_handlers;
    QMap<QString, AsyncHandler> m_asyncHandlers;
};

} // namespace liberaqt
