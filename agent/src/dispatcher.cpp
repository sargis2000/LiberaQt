#include "dispatcher.h"

#include "compat.h"
#include "idle_tracker.h"
#include "input_synth.h"
#include "meta_invoke.h"
#include "object_registry.h"
#include "screenshot.h"
#include "signal_waiter.h"
#include "selector_engine.h"
#include "value_codec.h"
#include "widget_backend.h"

#include <cstdlib>
#include <memory>

#include <QCoreApplication>
#include <QStringList>
#include <QTimer>
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

// Input handlers only *queue* their events: native input goes into the window-system queue, which
// the platform event dispatcher drains on its next pass. Answering straight away would race the
// application -- the client's next command could arrive before the click had been handled, which
// is exactly how a synthesised click ends up looking as though nothing happened at all. So every
// input command is asynchronous and replies once the queue has been drained.
//
// Two turns rather than one, because whether "flush the window-system queue" comes before or
// after "fire zero-timers" within a single pass is a property of the platform dispatcher, not
// something worth depending on. Neither turn costs wall-clock time. A nested event loop -- the
// modal dialog the click just opened -- runs zero-timers too, so the reply is never stranded.
void Dispatcher::registerInputCommand(const QString &name, Handler handler)
{
    registerAsyncCommand(name, [handler](const QVariantMap &params, Resolver resolve, Rejecter) {
        // Throwing here is fine: handle() wraps the call and turns it into a rejection.
        const QVariant result = handler(params);
        QTimer::singleShot(0, qApp, [resolve, result] {
            QTimer::singleShot(0, qApp, [resolve, result] { resolve(result); });
        });
    });
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
        const QString handle = params.value(QStringLiteral("handle")).toString();
        QObject *object = m_registry.resolve(handle);
        // A composite handle names a cell inside the view, so describe the cell, not the view.
        const QVariantMap info = WidgetBackend::describeItem(object, handle, m_registry);
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

    // ---- signals ---------------------------------------------------------
    // Asynchronous by necessity: the point is to let the application run until it emits.
    registerAsyncCommand(QStringLiteral("sync.wait_signal"),
                         [this](const QVariantMap &params, Resolver resolve, Rejecter reject) {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        const QString name = params.value(QStringLiteral("signal")).toString();
        const int timeoutMs = params.value(QStringLiteral("timeout_ms"), 5000).toInt();

        QString available;
        const bool started = SignalWaiter::start(
            object, name, timeoutMs,
            [resolve, reject, name, timeoutMs](bool emitted) {
                if (!emitted) {
                    reject(CommandError(ErrorCode::Timeout,
                                        QStringLiteral("'%1' was not emitted within %2 ms")
                                            .arg(name).arg(timeoutMs)));
                    return;
                }
                QVariantMap out;
                out.insert(QStringLiteral("emitted"), true);
                resolve(out);
            },
            &available);

        if (!started) {
            reject(CommandError(ErrorCode::Unsupported,
                                QStringLiteral("%1 has no signal '%2'; it has: %3")
                                    .arg(QString::fromUtf8(object->metaObject()->className()),
                                         name, available)));
        }
    });

    // ---- widgets / models ------------------------------------------------
    registerCommand(QStringLiteral("widget.item_rect"), [this](const QVariantMap &params) {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        return WidgetBackend::itemRect(object, params, m_registry);
    });

    registerCommand(QStringLiteral("widget.select_item"), [this](const QVariantMap &params) {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        return WidgetBackend::selectItem(object, params);
    });

    registerCommand(QStringLiteral("widget.tab_select"), [this](const QVariantMap &params) {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        return WidgetBackend::tabSelect(object, params);
    });

    registerCommand(QStringLiteral("widget.menu_trigger"), [this](const QVariantMap &params) {
        QObject *window = m_registry.resolve(params.value(QStringLiteral("window")).toString());
        return WidgetBackend::menuTrigger(window, params);
    });

    registerCommand(QStringLiteral("widget.model_data"),
                    [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        return WidgetBackend::modelData(object,
                                        params.value(QStringLiteral("max_rows"), -1).toInt());
    });

    // ---- input -----------------------------------------------------------
    registerInputCommand(QStringLiteral("input.click"), [this](const QVariantMap &params) {
        return InputSynth::click(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.hover"), [this](const QVariantMap &params) {
        return InputSynth::hover(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.key"), [this](const QVariantMap &params) {
        return InputSynth::key(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.type_text"), [this](const QVariantMap &params) {
        return InputSynth::typeText(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.press"), [this](const QVariantMap &params) {
        return InputSynth::press(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.release"), [this](const QVariantMap &params) {
        return InputSynth::release(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.wheel"), [this](const QVariantMap &params) {
        return InputSynth::wheel(m_registry, params);
    });
    registerInputCommand(QStringLiteral("input.drag"), [this](const QVariantMap &params) {
        return InputSynth::drag(m_registry, params);
    });

    // Not input, despite the name: it writes a property. Nothing is queued, so it stays
    // synchronous -- see the note on InputSynth::setText.
    registerCommand(QStringLiteral("input.set_text"), [this](const QVariantMap &params) -> QVariant {
        return InputSynth::setText(m_registry, params);
    });

    // ---- properties ------------------------------------------------------
    registerCommand(QStringLiteral("object.list_properties"),
                    [this](const QVariantMap &params) -> QVariant {
        QObject *object = m_registry.resolve(params.value(QStringLiteral("handle")).toString());
        QVariantMap out;
        out.insert(QStringLiteral("properties"), WidgetBackend::listProperties(object));
        return out;
    });

    // Deliberately never an error: "does this exist?" has a false answer, not a failure.
    registerCommand(QStringLiteral("object.exists"), [this](const QVariantMap &params) -> QVariant {
        const QString handle = params.value(QStringLiteral("handle")).toString();
        QVariantMap out;
        out.insert(QStringLiteral("exists"), m_registry.resolveOrNull(handle) != nullptr);
        return out;
    });

    registerCommand(QStringLiteral("session.set_options"),
                    [](const QVariantMap &params) -> QVariant {
        // Unknown keys are ignored rather than rejected, so a newer client can talk to an older
        // agent without failing outright. "accepted" therefore reports what was actually applied,
        // not what was sent -- that difference is how a client can tell the two apart.
        QStringList accepted;
        const QString inputMode = params.value(QStringLiteral("input_mode")).toString();
        if (!inputMode.isEmpty()) {
            InputSynth::Mode mode = InputSynth::Mode::Native;
            if (!InputSynth::parseMode(inputMode, &mode)) {
                throw CommandError(ErrorCode::InvalidParams,
                                   QStringLiteral("unknown input_mode '%1'; expected 'native' or "
                                                  "'synthetic'").arg(inputMode));
            }
            InputSynth::setMode(mode);
            accepted << QStringLiteral("input_mode");
        }
        QVariantMap out;
        out.insert(QStringLiteral("accepted"), accepted);
        return out;
    });

    registerCommand(QStringLiteral("screen.grab"), [this](const QVariantMap &params) -> QVariant {
        return Screenshot::grab(m_registry, params);
    });
}

} // namespace liberaqt
