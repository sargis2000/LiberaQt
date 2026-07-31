#pragma once

#include <QMap>
#include <QObject>
#include <QVariantMap>
#include <functional>

namespace qtdriver {

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

    explicit Dispatcher(ObjectRegistry &registry);

    // Returns a complete protocol response object for the given request.
    QVariantMap handle(const QVariantMap &request);

    void registerCommand(const QString &name, Handler handler);

private:
    void registerBuiltins();

    ObjectRegistry &m_registry;
    QMap<QString, Handler> m_handlers;
};

} // namespace qtdriver
