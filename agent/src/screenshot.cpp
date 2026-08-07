#include "screenshot.h"

#include "dispatcher.h"
#include "object_registry.h"

#include <QApplication>
#include <QBuffer>
#include <QPixmap>
#include <QWidget>
#include <QWindow>

namespace liberaqt {

QVariantMap Screenshot::grab(ObjectRegistry &registry, const QVariantMap &params)
{
    QObject *object = registry.resolveOrNull(params.value(QStringLiteral("handle")).toString());

    QPixmap pixmap;
    if (auto *widget = qobject_cast<QWidget *>(object)) {
        pixmap = widget->grab();
    } else if (auto *window = qobject_cast<QWindow *>(object)) {
        // TODO(m2): QQuickWindow::grabWindow() for Quick scenes -- grab() on the QWindow base
        // class does not capture the scene graph.
        if (QWidget *w = QWidget::find(window->winId()))
            pixmap = w->grab();
    } else if (QWidget *active = QApplication::activeWindow()) {
        pixmap = active->grab();
    }

    if (pixmap.isNull())
        throw CommandError(ErrorCode::Unsupported, QStringLiteral("nothing to grab"));

    QByteArray bytes;
    QBuffer buffer(&bytes);
    buffer.open(QIODevice::WriteOnly);
    pixmap.save(&buffer, "PNG");

    QVariantMap out;
    out.insert(QStringLiteral("png"), QString::fromLatin1(bytes.toBase64()));
    out.insert(QStringLiteral("size"), QVariantList{pixmap.width(), pixmap.height()});
    return out;
}

} // namespace liberaqt
