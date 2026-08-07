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

QVariantMap Dispatcher::handle(const QVariantMap &request)
{
    QVariantMap response;
    response.insert(QStringLiteral("id"), request.value(QStringLiteral("id")));

    const QString cmd = request.value(QStringLiteral("cmd")).toString();
    const auto it = m_handlers.constFind(cmd);
    if (it == m_handlers.constEnd()) {
        QVariantMap error;
        error.insert(QStringLiteral("code"), QString::fromLatin1(ErrorCode::Unsupported));
        error.insert(QStringLiteral("message"), QStringLiteral("unknown command: %1").arg(cmd));
        response.insert(QStringLiteral("ok"), false);
        response.insert(QStringLiteral("error"), error);
        return response;
    }

    // A handler must never let an exception escape into the Qt event loop.
    try {
        const QVariant result = (*it)(request.value(QStringLiteral("params")).toMap());
        response.insert(QStringLiteral("ok"), true);
        response.insert(QStringLiteral("result"), result);
    } catch (const CommandError &err) {
        QVariantMap error;
        error.insert(QStringLiteral("code"), err.code);
        error.insert(QStringLiteral("message"), err.message);
        error.insert(QStringLiteral("data"), err.data);
        response.insert(QStringLiteral("ok"), false);
        response.insert(QStringLiteral("error"), error);
    } catch (const std::exception &err) {
        QVariantMap error;
        error.insert(QStringLiteral("code"), QString::fromLatin1(ErrorCode::Internal));
        error.insert(QStringLiteral("message"), QString::fromUtf8(err.what()));
        response.insert(QStringLiteral("ok"), false);
        response.insert(QStringLiteral("error"), error);
    }
    return response;
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
        return MetaInvoke::call(object, method, args);
    });

    // TODO(m1): object.list_properties, quick.*, widget.*, record.*
    // Each new command needs: PROTOCOL.md entry, handler here, Python method, and a test.

    // ---- synchronisation -------------------------------------------------
    registerCommand(QStringLiteral("sync.wait_idle"), [](const QVariantMap &params) -> QVariant {
        const int quietMs = params.value(QStringLiteral("quiet_ms"), 50).toInt();
        const bool animations = params.value(QStringLiteral("animations"), true).toBool();
        const bool network = params.value(QStringLiteral("network"), false).toBool();
        const int timeoutMs = params.value(QStringLiteral("timeout_ms"), 10000).toInt();

        IdleTracker tracker;
        const int waited = tracker.waitForIdle(quietMs, animations, network, timeoutMs);
        if (waited < 0)
            throw CommandError(ErrorCode::Timeout,
                               QStringLiteral("UI did not become idle within %1 ms").arg(timeoutMs));
        QVariantMap out;
        out.insert(QStringLiteral("waited_ms"), waited);
        return out;
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
