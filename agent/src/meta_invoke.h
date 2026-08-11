#pragma once

#include <QString>
#include <QVariant>
#include <QVariantList>

class QObject;

namespace liberaqt {

// Calls a method by name through the meta-object system.
//
// Only slots and Q_INVOKABLE methods are reachable: everything else on a QObject is invisible to
// moc, so plain public functions such as QWidget::resize or QWidget::activateWindow cannot be
// called this way no matter how they are spelled. When lookup fails we say so explicitly and
// list what *is* invokable, because "unknown method" with no alternatives is a dead end.
class MetaInvoke
{
public:
    // Returns {"value": <encoded return value>}; the value is null for void methods.
    //
    // `queued` posts the call instead of making it, and returns {"queued": true} without waiting
    // for it to run. That is the only way to invoke something that opens a modal dialog: a direct
    // call does not return until the dialog closes, which strands the reply for as long as the
    // dialog is up. The return value is necessarily lost, so this is opt-in.
    static QVariant call(QObject *object, const QString &name, const QVariantList &args,
                         bool queued = false);
};

} // namespace liberaqt