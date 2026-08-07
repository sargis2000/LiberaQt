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
    static QVariant call(QObject *object, const QString &name, const QVariantList &args);
};

} // namespace liberaqt