#pragma once

#include <QVariantMap>
#include <QObject>

namespace liberaqt {

class ObjectRegistry;

// Converts raw user input into semantic, replayable actions.
//
// The hard part is not capturing events -- it is deciding WHICH object an action belongs to and
// WHICH selector will still find it next month. Rules:
//   * attribute a click to the widget that actually accepted the event, not the one under the
//     cursor (they differ for composite widgets: a click on a QPushButton's label child belongs
//     to the button);
//   * commit text on focus-out or Enter, emitting one fill() rather than 20 keystrokes;
//   * collapse press/release pairs, drags, and double-clicks into single actions;
//   * rank selectors: objectName > accessible name > unique type+text > type+index in a named
//     ancestor. Anything below that is emitted with a "brittle" flag.
class Recorder : public QObject
{
    Q_OBJECT
public:
    explicit Recorder(ObjectRegistry &registry);

    void start(const QString &granularity);
    void stop();
    bool isRecording() const { return m_recording; }

protected:
    bool eventFilter(QObject *watched, QEvent *event) override;

private:
    QString bestSelectorFor(QObject *object, bool *brittle) const;
    void emitAction(const QVariantMap &action);

    ObjectRegistry &m_registry;
    bool m_recording = false;
    QString m_granularity;
};

} // namespace liberaqt
