#include "dispatcher.h"

#include "compat.h"
#include "idle_tracker.h"
#include "input_synth.h"
#include "meta_invoke.h"
#include "object_registry.h"
#include "screenshot.h"
#include "selector_engine.h"
#include "value_codec.h"
#include "widget_backend.h"

#include <cstdlib>
#include <memory>

#include <QCoreApplication>
#include <QVariantList>

namespace liberaqt {

namespace ErrorCode {
const char *NotFound = "not_found";
const char *Ambiguous = "ambiguous";
const char *Stale = "stale";
const char *NotActionable = "not_actionable";
const char *Unsupported = "unsupported";
const char *InvalidParams = "invalid_params";
const char *Internal = "internal";
const char *Timeout = "timeout";
}

Dispatcher::Dispatcher(ObjectRegistry &registry)
    : m_registry(registry)
{
    registerBuiltins();
}

void Dispatcher::registerCommand(const QString &name, Handler handler)
{
    m_handlers.insert(name, std::move(handler));
}

void Dispatcher::registerAsyncCommand(const QString &name, AsyncHandler handler)
{
    m_asyncHandlers.insert(name, std::move(handler));
}

namespace {

QVariantMap okResponse(const QVariant &id, const QVariant &result)
{
    QVariantMap response;
    response.insert(QStringLiteral("id"), id);
    response.insert(QStringLiteral("ok"), true);
    response.insert(QStringLiteral("result"), result);
    return response;
}

QVariantMap errorResponse(const QVariant &id, const QString &code, const QString &message,
                          const QVariantMap &data = {})
{
    QVariantMap error;
    error.insert(QStringLiteral("code"), code);
    error.insert(QStringLiteral("message"), message);
    if (!data.isEmpty())
        error.insert(QStringLiteral("data"), data);
    QVariantMap response;
    response.insert(QStringLiteral("id"), id);
    response.insert(QStringLiteral("ok"), false);
    response.insert(QStringLiteral("error"), error);
    return response;
}

} // namespace

void Dispatcher::handle(const QVariantMap &request, Reply reply)
{
    const QVariant id = request.value(QStringLiteral("id"));
    const QString cmd = request.value(QStringLiteral("cmd")).toString();
    const QVariantMap params = request.value(QStringLiteral("params")).toMap();

    const auto async = m_asyncHandlers.constFind(cmd);
    if (async != m_asyncHandlers.constEnd()) {
        // Guard against a handler that resolves twice, or resolves after rejecting: the client
        // correlates on id, so a duplicate reply would be attributed to a later command.
        auto answered = std::make_shared<bool>(false);
        Resolver resolve = [reply, id, answered](const QVariant &result) {
            if (*answered)
                return;
            *answered = true;
            reply(okResponse(id, result));
        };
        Rejecter reject = [reply, id, answered](const CommandError &err) {
            if (*answered)
                return;
            *answered = true;
            reply(errorResponse(id, err.code, err.message, err.data));
        };
        try {
            (*async)(params, resolve, reject);
        } catch (const CommandError &err) {
            reject(err);
        } catch (const std::exception &err) {
            reject(CommandError(ErrorCode::Internal, QString::fromUtf8(err.what())));
        }
        return;
    }

    const auto it = m_handlers.constFind(cmd);
    if (it == m_handlers.constEnd()) {
        reply(errorResponse(id, QString::fromLatin1(ErrorCode::Unsupported),
                            QStringLiteral("unknown command: %1").arg(cmd)));
        return;
    }

    // A handler must never let an exception escape into the Qt event loop.
    try {
        reply(okResponse(id, (*it)(params)));
    } catch (const CommandError &err) {
        reply(errorResponse(id, err.code, err.message, err.data));
    } catch (const std::exception &err) {
        reply(errorResponse(id, QString::fromLatin1(ErrorCode::Internal),
                            QString::fromUtf8(err.what())));
    }
}

void Dispatcher::registerBuiltins()
{
    // ---- session ---------------------------------------------------------
    registerCommand(QStringLiteral("session.ping"), [](const QVariantMap &) -> QVariant {
        QVariantMap out;
        out.insert(QStringLiteral("pong"), true);
        return out;
    });

    registerCommand(QStringLiteral("session.info"), [](const QVariantMap &) -> QVariant {
        QVariantMap out;
        out.insert(QStringLiteral("qt"), QString::fromUtf8(qVersion()));
        out.insert(QStringLiteral("pid"), QCoreApplication::applicationPid());
        out.insert(QStringLiteral("app"), QCoreApplication::applicationName());
        return out;
    });

    registerCommand(QStringLiteral("session.quit"), [](const QVariantMap &params) -> QVariant {
        const bool force = params.value(QStringLiteral("force")).toBool();
        if (force)
            std::_Exit(0);
        QCoreApplication::quit();
        return QVariantMap{};
    });

    // ---- discovery -------------------------------------------------------
    registerCommand(QStringLiteral("window.list"), [this](const QVariantMap &) -> QVariant {
        return WidgetBackend::listWindows(m_registry);
    });

    registerCommand(QStringLiteral("object.find"), [this](const QVariantMap &params) -> QVariant {
        const Selector selector = Selector::fromJson(
            params.value(QStringLiteral("selector")).toMap());
        QObject *root = m_registry.resolveOrNull(
            params.value(QStringLiteral("root")).toString());

        SelectorEngine engine(m_registry);
        const auto matches = engine.find(selector, root,
                                         params.value(QStringLiteral("limit")).toInt());

        QVariantList handles;
        for (QObject *o : matches)
            handles.append(m_registry.handleFor(o));

        QVariantMap out;
        out.insert(QStringLiteral("handles"), handles);
        if (handles.isEmpty()) {
            // Near misses are what turn "not found" from a dead end into a diagnosis.
            out.insert(QStringLiteral("near_misses"), engine.nearMisses(selector, root, 5));
        }
        return out;
    });

    registerCommand(QStringLiteral("object.info"), [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        const QVariantMap info = WidgetBackend::describe(object, m_registry);
        if (params.value(QStringLiteral("require_actionable")).toBool()) {
            const QString why = WidgetBackend::actionabilityProblem(object);
            if (!why.isEmpty())
                throw CommandError(ErrorCode::NotActionable, why, info);
        }
        return info;
    });

    registerCommand(QStringLiteral("object.tree"), [this](const QVariantMap &params) -> QVariant {
        QObject *root = m_registry.resolveOrNull(params.value(QStringLiteral("root")).toString());
        return WidgetBackend::dumpTree(root, m_registry,
                                       params.value(QStringLiteral("depth"), -1).toInt(),
                                       params.value(QStringLiteral("visual_only"), true).toBool());
    });

    // ---- properties ------------------------------------------------------
    registerCommand(QStringLiteral("object.get_property"),
                    [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        const QString name = params.value(QStringLiteral("name")).toString();
        QVariantMap out;
        out.insert(QStringLiteral("value"),
                   ValueCodec::encode(object->property(name.toUtf8().constData())));
        return out;
    });

    registerCommand(QStringLiteral("object.set_property"),
                    [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        const QString name = params.value(QStringLiteral("name")).toString();
        QVariant value = ValueCodec::decode(params.value(QStringLiteral("value")));

        // Shape the value to the declared property type first, so JSON arrays can reach
        // QSize/QPoint/QRect properties -- which is how geometry is written (QWidget exposes
        // size and pos as properties whose setters are resize() and move()).
        const QMetaObject *mo = object->metaObject();
        const int index = mo->indexOfProperty(name.toUtf8().constData());
        if (index >= 0)
            value = ValueCodec::coerce(value, compat::propertyTypeId(mo->property(index)));

        const bool ok = object->setProperty(name.toUtf8().constData(), value);
        if (!ok)
            throw CommandError(ErrorCode::Unsupported,
                               QStringLiteral("no writable property '%1' on %2")
                                   .arg(name, QString::fromUtf8(object->metaObject()->className())));
        return QVariantMap{};
    });

    registerCommand(QStringLiteral("object.invoke"), [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        const QString method = params.value(QStringLiteral("method")).toString();
        if (method.isEmpty())
            throw CommandError(ErrorCode::InvalidParams, QStringLiteral("'method' is required"));
        const QVariantList args = params.value(QStringLiteral("args")).toList();
        // "__" names are operations Qt does not expose as slots; see WidgetBackend::synthetic.
        if (method.startsWith(QLatin1String("__")))
            return WidgetBackend::synthetic(object, method, args);
        return MetaInvoke::call(object, method, args,
                                params.value(QStringLiteral("queued")).toBool());
    });

    // TODO(m1): object.list_properties, quick.*, widget.*, record.*
    // Each new command needs: PROTOCOL.md entry, handler here, Python method, and a test.

    // ---- synchronisation -------------------------------------------------
    // Asynchronous: the wait is driven by the event loop rather than pumping it, so the reply
    // still arrives if the application enters a nested loop (a modal dialog) while we wait.
    registerAsyncCommand(QStringLiteral("sync.wait_idle"),
                         [](const QVariantMap &params, Resolver resolve, Rejecter reject) {
        const int quietMs = params.value(QStringLiteral("quiet_ms"), 50).toInt();
        const bool animations = params.value(QStringLiteral("animations"), true).toBool();
        const bool network = params.value(QStringLiteral("network"), false).toBool();
        const int timeoutMs = params.value(QStringLiteral("timeout_ms"), 10000).toInt();

        // Owned by the event loop: deletes itself once it has reported.
        auto *tracker = new IdleTracker(QCoreApplication::instance());
        tracker->waitForIdle(quietMs, animations, network, timeoutMs,
                             [resolve, reject, timeoutMs](int waited) {
            if (waited < 0) {
                reject(CommandError(ErrorCode::Timeout,
                                    QStringLiteral("UI did not become idle within %1 ms")
                                        .arg(timeoutMs)));
                return;
            }
            QVariantMap out;
            out.insert(QStringLiteral("waited_ms"), waited);
            resolve(out);
        });
    });

    // ---- widgets / models ------------------------------------------------
    registerCommand(QStringLiteral("widget.model_data"),
                    [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        return WidgetBackend::modelData(object,
                                        params.value(QStringLiteral("max_rows"), -1).toInt());
    });

    // ---- input -----------------------------------------------------------
    registerCommand(QStringLiteral("input.click"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::click(m_registry, params);
    });
    registerCommand(QStringLiteral("input.hover"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::hover(m_registry, params);
    });
    registerCommand(QStringLiteral("input.key"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::key(m_registry, params);
    });
    registerCommand(QStringLiteral("input.type_text"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::typeText(m_registry, params);
    });
    registerCommand(QStringLiteral("input.set_text"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::setText(m_registry, params);
    });

    // ---- visual ----------------------------------------------------------
    registerCommand(QStringLiteral("screen.grab"), [this](const QVariantMap &params) -> QVariant {
        return Screenshot::grab(m_registry, params);
    });
}

} // namespace liberaqt
