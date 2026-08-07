#include "recorder.h"

#include "agent.h"
#include "object_registry.h"
#include "selector_engine.h"

#include <QCoreApplication>
#include <QEvent>
#include <QMouseEvent>
#include <QVariantMap>

namespace liberaqt {

Recorder::Recorder(ObjectRegistry &registry)
    : m_registry(registry)
{
}

void Recorder::start(const QString &granularity)
{
    if (m_recording)
        return;
    m_granularity = granularity;
    m_recording = true;
    QCoreApplication::instance()->installEventFilter(this);
}

void Recorder::stop()
{
    if (!m_recording)
        return;
    QCoreApplication::instance()->removeEventFilter(this);
    m_recording = false;
}

bool Recorder::eventFilter(QObject *watched, QEvent *event)
{
    if (!m_recording)
        return false;

    // Never consume the event: recording must not change how the app behaves.
    switch (event->type()) {
    case QEvent::MouseButtonRelease: {
        auto *mouse = static_cast<QMouseEvent *>(event);
        bool brittle = false;
        QVariantMap action;
        action.insert(QStringLiteral("action"), QStringLiteral("click"));
        action.insert(QStringLiteral("selector"), bestSelectorFor(watched, &brittle));
        action.insert(QStringLiteral("brittle"), brittle);
        action.insert(QStringLiteral("button"),
                      mouse->button() == Qt::RightButton ? QStringLiteral("right")
                                                         : QStringLiteral("left"));
        emitAction(action);
        break;
    }
    case QEvent::FocusOut: {
        // TODO(m4): emit a fill() action carrying the committed text, if it changed since focus-in.
        break;
    }
    default:
        break;
    }
    return false;
}

QString Recorder::bestSelectorFor(QObject *object, bool *brittle) const
{
    *brittle = false;
    const QString className = QString::fromUtf8(object->metaObject()->className());

    if (!object->objectName().isEmpty())
        return className + QLatin1Char('#') + object->objectName();

    const QString text = SelectorEngine::displayText(object);
    if (!text.isEmpty()) {
        // TODO(m4): verify uniqueness by re-running the selector before emitting it.
        return QStringLiteral("%1[text='%2']").arg(className, text);
    }

    *brittle = true;
    return className;
}

void Recorder::emitAction(const QVariantMap &action)
{
    Agent::instance().emitEvent(QStringLiteral("record.action"), action);
}

} // namespace liberaqt
