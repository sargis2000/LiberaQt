#include "widget_backend.h"

#include "dispatcher.h"
#include "object_registry.h"
#include "selector_engine.h"
#include "value_codec.h"

#include <QAbstractItemModel>
#include <QAbstractItemView>
#include <QApplication>
#include <QGuiApplication>
#include <QRect>
#include <QPoint>
#include <QWidget>
#include <QWindow>

namespace qtdriver {

QVariantList WidgetBackend::listWindows(ObjectRegistry &registry)
{
    QVariantList out;
    const auto widgets = QApplication::topLevelWidgets();
    for (QWidget *widget : widgets) {
        if (!widget->isVisible())
            continue;
        QVariantMap entry;
        entry.insert(QStringLiteral("handle"), registry.handleFor(widget));
        entry.insert(QStringLiteral("title"), widget->windowTitle());
        entry.insert(QStringLiteral("window_type"), QStringLiteral("widget"));
        entry.insert(QStringLiteral("active"), widget->isActiveWindow());
        const QRect g = widget->geometry();
        entry.insert(QStringLiteral("geometry"),
                     QVariantList{g.x(), g.y(), g.width(), g.height()});
        out.append(entry);
    }

    // Quick-only applications have no top-level widgets at all.
    const auto windows = QGuiApplication::topLevelWindows();
    for (QWindow *window : windows) {
        if (!window->isVisible())
            continue;
        if (QWidget::find(window->winId()))
            continue; // backing window of a QWidget, already reported above
        QVariantMap entry;
        entry.insert(QStringLiteral("handle"), registry.handleFor(window));
        entry.insert(QStringLiteral("title"), window->title());
        entry.insert(QStringLiteral("window_type"), QStringLiteral("quick"));
        entry.insert(QStringLiteral("active"), window->isActive());
        const QRect g = window->geometry();
        entry.insert(QStringLiteral("geometry"),
                     QVariantList{g.x(), g.y(), g.width(), g.height()});
        out.append(entry);
    }
    return out;
}

QVariantMap WidgetBackend::modelData(QObject *object, int maxRows)
{
    auto *view = qobject_cast<QAbstractItemView *>(object);
    if (!view) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("%1 is not a model-backed item view")
                               .arg(QString::fromUtf8(object->metaObject()->className())));
    }
    QAbstractItemModel *model = view->model();
    if (!model)
        throw CommandError(ErrorCode::Unsupported, QStringLiteral("the view has no model"));

    const int columns = model->columnCount();
    QStringList headers;
    headers.reserve(columns);
    for (int c = 0; c < columns; ++c) {
        QString header = model->headerData(c, Qt::Horizontal, Qt::DisplayRole).toString();
        if (header.isEmpty())
            header = QString::number(c);
        headers.append(header);
    }

    int rowCount = model->rowCount();
    if (maxRows >= 0 && maxRows < rowCount)
        rowCount = maxRows;

    QVariantList rows;
    for (int r = 0; r < rowCount; ++r) {
        QVariantMap row;
        for (int c = 0; c < columns; ++c)
            row.insert(headers.at(c),
                       ValueCodec::encode(model->data(model->index(r, c), Qt::DisplayRole)));
        rows.append(row);
    }

    QVariantMap out;
    out.insert(QStringLiteral("rows"), rows);
    out.insert(QStringLiteral("headers"), QVariant(headers));
    return out;
}

QVariantMap WidgetBackend::describe(QObject *object, ObjectRegistry &registry)
{
    QVariantMap info;
    info.insert(QStringLiteral("handle"), registry.handleFor(object));
    info.insert(QStringLiteral("class"), QString::fromUtf8(object->metaObject()->className()));
    info.insert(QStringLiteral("objectName"), object->objectName());
    info.insert(QStringLiteral("text"), SelectorEngine::displayText(object));

    if (auto *widget = qobject_cast<QWidget *>(object)) {
        const QRect g = widget->geometry();
        info.insert(QStringLiteral("geometry"),
                    QVariantList{g.x(), g.y(), g.width(), g.height()});
        info.insert(QStringLiteral("visible"), widget->isVisible());
        info.insert(QStringLiteral("enabled"), widget->isEnabled());
        info.insert(QStringLiteral("focused"), widget->hasFocus());
        info.insert(QStringLiteral("title"), widget->windowTitle());
        info.insert(QStringLiteral("active"), widget->isActiveWindow());
    } else if (auto *window = qobject_cast<QWindow *>(object)) {
        const QRect g = window->geometry();
        info.insert(QStringLiteral("geometry"),
                    QVariantList{g.x(), g.y(), g.width(), g.height()});
        info.insert(QStringLiteral("visible"), window->isVisible());
        info.insert(QStringLiteral("enabled"), true);
        info.insert(QStringLiteral("title"), window->title());
        info.insert(QStringLiteral("active"), window->isActive());
    } else {
        info.insert(QStringLiteral("visible"), object->property("visible"));
        info.insert(QStringLiteral("enabled"), object->property("enabled"));
    }

    const QVariant checked = object->property("checked");
    if (checked.isValid())
        info.insert(QStringLiteral("checked"), checked.toBool());
    const QVariant value = object->property("value");
    if (value.isValid())
        info.insert(QStringLiteral("value"), value);

    return info;
}

QString WidgetBackend::actionabilityProblem(QObject *object)
{
    auto *widget = qobject_cast<QWidget *>(object);
    if (!widget) {
        const QVariant visible = object->property("visible");
        if (visible.isValid() && !visible.toBool())
            return QStringLiteral("object is not visible");
        const QVariant enabled = object->property("enabled");
        if (enabled.isValid() && !enabled.toBool())
            return QStringLiteral("object is not enabled");
        return {};
    }

    if (!widget->isVisible())
        return QStringLiteral("widget is hidden");
    if (!widget->isEnabled())
        return QStringLiteral("widget is disabled");
    if (widget->size().isEmpty())
        return QStringLiteral("widget has zero size");

    // TODO(m1): detect obscuring siblings and modal dialogs covering the target, and report which
    // widget is in the way -- that is usually the actual bug the test found.
    return {};
}

bool WidgetBackend::interactionPoint(QObject *object, QPoint *out)
{
    if (auto *widget = qobject_cast<QWidget *>(object)) {
        *out = widget->rect().center();
        return true;
    }
    if (auto *window = qobject_cast<QWindow *>(object)) {
        *out = QPoint(window->width() / 2, window->height() / 2);
        return true;
    }
    // TODO(m2): QQuickItem -> mapToScene(boundingRect().center())
    return false;
}

QVariantMap WidgetBackend::dumpTree(QObject *root, ObjectRegistry &registry, int depth,
                                    bool visualOnly)
{
    QVariantMap node;
    if (!root)
        return node;

    node.insert(QStringLiteral("handle"), registry.handleFor(root));
    node.insert(QStringLiteral("class"), QString::fromUtf8(root->metaObject()->className()));
    node.insert(QStringLiteral("objectName"), root->objectName());
    node.insert(QStringLiteral("text"), SelectorEngine::displayText(root));

    if (depth == 0)
        return node;

    QVariantList children;
    const auto kids = root->children();
    for (QObject *child : kids) {
        if (visualOnly && !qobject_cast<QWidget *>(child) && !qobject_cast<QWindow *>(child))
            continue;
        children.append(dumpTree(child, registry, depth > 0 ? depth - 1 : -1, visualOnly));
    }
    node.insert(QStringLiteral("children"), children);
    return node;
}

} // namespace qtdriver
