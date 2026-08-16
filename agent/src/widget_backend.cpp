#include "widget_backend.h"

#include "dispatcher.h"
#include "input_synth.h"
#include "object_registry.h"
#include "selector_engine.h"
#include "value_codec.h"

#include <QAbstractItemModel>
#include <QAbstractItemView>
#include <QAbstractSpinBox>
#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QRadioButton>
#include <QStyleOptionButton>
#include <QItemSelectionModel>
#include <QMenu>
#include <QMenuBar>
#include <QMetaObject>
#include <QMetaProperty>
#include <QStyle>
#include <QStyleOptionSpinBox>
#include <QTabBar>
#include <QTabWidget>
#include <QGuiApplication>
#include <QRect>
#include <QPoint>
#include <QWidget>
#include <QWindow>

namespace liberaqt {

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

QVariant WidgetBackend::synthetic(QObject *object, const QString &name, const QVariantList &)
{
    QVariantMap out;
    out.insert(QStringLiteral("value"), QVariant());

    if (name == QLatin1String("__activate")) {
        // QWidget::activateWindow and QWindow::requestActivate ask the window manager to focus
        // the window; raise() reorders it locally. Tests generally want both.
        if (auto *widget = qobject_cast<QWidget *>(object)) {
            widget->raise();
            widget->activateWindow();
            return out;
        }
        if (auto *window = qobject_cast<QWindow *>(object)) {
            window->raise();
            window->requestActivate();
            return out;
        }
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("%1 is not a window, so it cannot be activated")
                               .arg(QString::fromUtf8(object->metaObject()->className())));
    }

    throw CommandError(ErrorCode::Unsupported,
                       QStringLiteral("unknown synthetic method '%1'").arg(name));
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
    // A checkbox or radio button only reacts over its indicator and label; a layout routinely
    // stretches the widget far wider than that, leaving the geometric centre in a dead zone a
    // user's click would miss too. Aim where the style says the clickable region is. Found by
    // Designer's startup checkbox: 529px wide, ~200px of it clickable.
    if (auto *check = qobject_cast<QCheckBox *>(object)) {
        QStyleOptionButton option;
        option.initFrom(check);
        option.text = check->text();
        *out = check->style()->subElementRect(QStyle::SE_CheckBoxClickRect, &option, check)
                   .center();
        return true;
    }
    if (auto *radio = qobject_cast<QRadioButton *>(object)) {
        QStyleOptionButton option;
        option.initFrom(radio);
        option.text = radio->text();
        *out = radio->style()->subElementRect(QStyle::SE_RadioButtonClickRect, &option, radio)
                   .center();
        return true;
    }
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

// ---------------------------------------------------------------- item views

namespace {

//: Separates a view handle from the cell coordinates appended to it.
const QChar ItemSeparator = QLatin1Char('~');

QAbstractItemView *asView(QObject *object)
{
    auto *view = qobject_cast<QAbstractItemView *>(object);
    if (!view) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("%1 is not an item view")
                               .arg(QString::fromUtf8(object->metaObject()->className())));
    }
    return view;
}

// Resolves a column given either an index or a header caption, which is how tests usually think
// of one ("the Part Number column", not "column 17").
int columnFor(QAbstractItemModel *model, const QVariant &column)
{
    if (!column.isValid() || column.isNull())
        return 0;
    bool numeric = false;
    const int index = column.toInt(&numeric);
    if (numeric)
        return index;
    const QString caption = column.toString();
    for (int c = 0; c < model->columnCount(); ++c) {
        if (model->headerData(c, Qt::Horizontal, Qt::DisplayRole).toString() == caption)
            return c;
    }
    throw CommandError(ErrorCode::NotFound, QStringLiteral("no column headed '%1'").arg(caption));
}

// Depth-first search of the whole model for a cell whose display text matches.
//
// Recursive because a tree keeps its children under their parent index rather than in the root's
// row count, so a flat scan sees only the top level -- which is almost never where the interesting
// rows are. Every column is searched too: callers name a cell by what they can see, and which
// column it happens to live in is a detail they should not have to know.
QModelIndex searchByText(QAbstractItemModel *model, const QModelIndex &parent,
                         const QString &wanted, bool contains)
{
    const int rows = model->rowCount(parent);
    const int columns = model->columnCount(parent);
    for (int r = 0; r < rows; ++r) {
        for (int c = 0; c < columns; ++c) {
            const QModelIndex index = model->index(r, c, parent);
            const QString text = model->data(index, Qt::DisplayRole).toString();
            if (contains ? text.contains(wanted) : text == wanted)
                return index;
        }
        // Children hang off column 0, whatever the matching column turns out to be.
        const QModelIndex first = model->index(r, 0, parent);
        // Lazy models only create their children once something asks for them, so a node that has
        // never been expanded reports zero rows. Real applications are full of these -- a file
        // tree, a design hierarchy -- and without this the search silently sees only the top
        // level and reports the item as missing.
        if (model->canFetchMore(first))
            model->fetchMore(first);
        if (model->hasChildren(first)) {
            const QModelIndex found = searchByText(model, first, wanted, contains);
            if (found.isValid())
                return found;
        }
    }
    return {};
}

QModelIndex findIndex(QAbstractItemView *view, const QVariantMap &params)
{
    QAbstractItemModel *model = view->model();
    if (!model)
        throw CommandError(ErrorCode::Unsupported, QStringLiteral("the view has no model"));

    const QVariant text = params.value(QStringLiteral("text"));
    const QVariant row = params.value(QStringLiteral("row"));
    const int column = columnFor(model, params.value(QStringLiteral("column")));

    if (text.isValid() && !text.isNull()) {
        const QString wanted = text.toString();
        // row(has_text=) means containing; select_item(text=) means exactly.
        const bool contains =
            params.value(QStringLiteral("match")).toString() == QLatin1String("contains");
        const QModelIndex found = searchByText(model, QModelIndex(), wanted, contains);
        if (found.isValid())
            return found;
        throw CommandError(ErrorCode::NotFound, QStringLiteral("no item reads '%1'").arg(wanted));
    }

    if (row.isValid() && !row.isNull()) {
        const int r = row.toInt();
        if (r < 0 || r >= model->rowCount()) {
            throw CommandError(ErrorCode::NotFound,
                               QStringLiteral("row %1 is out of range; the view has %2")
                                   .arg(r).arg(model->rowCount()));
        }
        return model->index(r, column);
    }

    throw CommandError(ErrorCode::InvalidParams,
                       QStringLiteral("give either 'text' or 'row' to identify an item"));
}

} // namespace

bool WidgetBackend::splitItemHandle(const QString &handle, QString *base, QList<int> *rows,
                                    int *column)
{
    const QStringList parts = handle.split(ItemSeparator);
    if (parts.size() != 3)
        return false;
    if (base)
        *base = parts.at(0);
    if (rows) {
        rows->clear();
        const QStringList chain = parts.at(1).split(QLatin1Char('/'), Qt::SkipEmptyParts);
        for (const QString &step : chain)
            rows->append(step.toInt());
    }
    if (column)
        *column = parts.at(2).toInt();
    return true;
}

namespace {

// Rebuilds the model index a composite handle names, walking the row chain from the root down.
QModelIndex indexFromHandle(QAbstractItemView *view, const QString &handle)
{
    QList<int> rows;
    int column = 0;
    if (!WidgetBackend::splitItemHandle(handle, nullptr, &rows, &column) || rows.isEmpty())
        return {};
    QAbstractItemModel *model = view->model();
    if (!model)
        return {};
    QModelIndex index;
    for (int depth = 0; depth < rows.size(); ++depth) {
        // Children hang off column 0; only the final step uses the requested column.
        const int col = depth + 1 == rows.size() ? column : 0;
        index = model->index(rows.at(depth), col, index);
        if (!index.isValid())
            return {};
    }
    return index;
}

// The inverse: the chain of rows from the root down to this index.
QString rowChain(const QModelIndex &index)
{
    QStringList chain;
    for (QModelIndex step = index; step.isValid(); step = step.parent())
        chain.prepend(QString::number(step.row()));
    return chain.join(QLatin1Char('/'));
}

} // namespace

QVariantMap WidgetBackend::itemRect(QObject *object, const QVariantMap &params,
                                    ObjectRegistry &registry)
{
    QAbstractItemView *view = asView(object);
    const QModelIndex index = findIndex(view, params);

    // Scrolling first is not optional: visualRect() of an off-screen row is empty, so a click
    // computed from it would land on whatever happens to occupy that corner of the viewport.
    view->scrollTo(index, QAbstractItemView::EnsureVisible);
    const QRect rect = view->visualRect(index);

    QVariantMap out;
    out.insert(QStringLiteral("handle"),
               registry.handleFor(view) + ItemSeparator + rowChain(index)
                   + ItemSeparator + QString::number(index.column()));
    out.insert(QStringLiteral("row"), index.row());
    out.insert(QStringLiteral("column"), index.column());
    out.insert(QStringLiteral("text"), index.data(Qt::DisplayRole).toString());
    out.insert(QStringLiteral("rect"),
               QVariantList{rect.x(), rect.y(), rect.width(), rect.height()});
    return out;
}

bool WidgetBackend::interactionPointFor(QObject *object, const QString &handle, QPoint *out)
{
    auto *view = qobject_cast<QAbstractItemView *>(object);
    if (view) {
        const QModelIndex index = indexFromHandle(view, handle);
        if (index.isValid()) {
            view->scrollTo(index, QAbstractItemView::EnsureVisible);
            const QRect rect = view->visualRect(index);
            if (rect.isValid()) {
                // Mapped out of the viewport, because that is the widget events are sent to.
                *out = view->viewport()->mapTo(view, rect.center());
                return true;
            }
        }
    }
    return interactionPoint(object, out);
}

QVariantMap WidgetBackend::describeItem(QObject *object, const QString &handle,
                                        ObjectRegistry &registry)
{
    auto *view = qobject_cast<QAbstractItemView *>(object);
    if (!view)
        return describe(object, registry);
    const QModelIndex index = indexFromHandle(view, handle);
    if (!index.isValid())
        return describe(object, registry);

    view->scrollTo(index, QAbstractItemView::EnsureVisible);
    const QRect rect = view->visualRect(index);

    QVariantMap info;
    info.insert(QStringLiteral("handle"), handle);
    info.insert(QStringLiteral("class"), QString::fromUtf8(view->metaObject()->className()));
    info.insert(QStringLiteral("objectName"), view->objectName());
    info.insert(QStringLiteral("text"), index.data(Qt::DisplayRole).toString());
    info.insert(QStringLiteral("row"), index.row());
    info.insert(QStringLiteral("column"), index.column());
    info.insert(QStringLiteral("geometry"),
                QVariantList{rect.x(), rect.y(), rect.width(), rect.height()});
    // A cell counts as visible only when the view is showing and the cell is inside the viewport.
    info.insert(QStringLiteral("visible"),
                view->isVisible() && view->viewport()->rect().intersects(rect));
    info.insert(QStringLiteral("enabled"),
                view->isEnabled() && index.flags().testFlag(Qt::ItemIsEnabled));
    const QVariant check = index.data(Qt::CheckStateRole);
    if (check.isValid())
        info.insert(QStringLiteral("checked"), check.toInt() == Qt::Checked);
    return info;
}

int WidgetBackend::comboEntry(QComboBox *combo, const QVariantMap &params)
{
    const QVariant text = params.value(QStringLiteral("text"));
    if (text.isValid() && !text.isNull()) {
        const int target = combo->findText(text.toString());
        if (target < 0) {
            QStringList available;
            for (int i = 0; i < combo->count(); ++i)
                available.append(combo->itemText(i));
            throw CommandError(ErrorCode::NotFound,
                               QStringLiteral("no entry '%1'; there is: %2")
                                   .arg(text.toString(), available.join(QStringLiteral(", "))));
        }
        return target;
    }
    const QVariant index = params.value(QStringLiteral("index"));
    const int target = index.isValid() && !index.isNull()
                           ? index.toInt()
                           : params.value(QStringLiteral("row"), -1).toInt();
    if (target < 0 || target >= combo->count()) {
        throw CommandError(ErrorCode::NotFound,
                           QStringLiteral("entry %1 is out of range; there are %2")
                               .arg(target).arg(combo->count()));
    }
    return target;
}

QVariantMap WidgetBackend::selectItem(QObject *object, const QVariantMap &params)
{
    // Combo boxes never reach this: the dispatcher routes them to the popup-clicking walker,
    // because their view only exists while the popup is open.
    QAbstractItemView *view = asView(object);
    const QModelIndex index = findIndex(view, params);
    if (!index.flags().testFlag(Qt::ItemIsEnabled)) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("the item at row %1 is disabled").arg(index.row()));
    }

    view->scrollTo(index, QAbstractItemView::EnsureVisible);
    if (InputSynth::modeOf(params) == InputSynth::Mode::Native) {
        // Selecting is what happens when a user clicks the item, so that is what this does --
        // which selection the click produces (row, cell, toggle) is the view's own policy,
        // exactly as it would be for the user.
        const QRect rect = view->visualRect(index);
        if (rect.isEmpty()) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("the item at row %1 has no geometry to click")
                                   .arg(index.row()));
        }
        InputSynth::clickNative(view->viewport(), rect.center());
    } else {
        view->setCurrentIndex(index);
        if (QItemSelectionModel *selection = view->selectionModel()) {
            selection->select(index,
                             QItemSelectionModel::ClearAndSelect | QItemSelectionModel::Rows);
        }
    }

    QVariantMap out;
    out.insert(QStringLiteral("row"), index.row());
    out.insert(QStringLiteral("column"), index.column());
    out.insert(QStringLiteral("text"), index.data(Qt::DisplayRole).toString());
    return out;
}

QVariantMap WidgetBackend::tabSelect(QObject *object, const QVariantMap &params)
{
    // Either half of the pair is accepted: tests say "the tab widget", but the tabs live on its
    // bar, and which one a selector lands on is an implementation detail of the application.
    auto *bar = qobject_cast<QTabBar *>(object);
    auto *tabs = qobject_cast<QTabWidget *>(object);
    if (!bar && !tabs) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("%1 is neither a QTabWidget nor a QTabBar")
                               .arg(QString::fromUtf8(object->metaObject()->className())));
    }
    const int count = bar ? bar->count() : tabs->count();

    const QVariant text = params.value(QStringLiteral("text"));
    int target = -1;
    if (text.isValid() && !text.isNull()) {
        const QString wanted = text.toString();
        for (int i = 0; i < count; ++i) {
            QString label = bar ? bar->tabText(i) : tabs->tabText(i);
            // Tab captions carry '&' accelerators that the user never sees.
            if (label.remove(QLatin1Char('&')) == wanted) {
                target = i;
                break;
            }
        }
        if (target < 0) {
            // Listing what is there turns a failed lookup into a usable diagnosis, the same way
            // the menu walker does.
            QStringList available;
            for (int i = 0; i < count; ++i)
                available.append(bar ? bar->tabText(i) : tabs->tabText(i));
            throw CommandError(ErrorCode::NotFound,
                               QStringLiteral("no tab labelled '%1'; there is: %2")
                                   .arg(wanted, available.join(QStringLiteral(", "))));
        }
    } else {
        target = params.value(QStringLiteral("index"), -1).toInt();
        if (target < 0 || target >= count) {
            throw CommandError(ErrorCode::NotFound,
                               QStringLiteral("tab %1 is out of range; there are %2")
                                   .arg(target).arg(count));
        }
    }

    // From here on the bar is what matters even when the caller named the QTabWidget: the tabs
    // a user sees and clicks live on it.
    QTabBar *clickBar = bar ? bar : tabs->tabBar();
    if (!clickBar->isTabEnabled(target)) {
        throw CommandError(ErrorCode::NotActionable,
                           QStringLiteral("the tab '%1' is disabled")
                               .arg(clickBar->tabText(target).remove(QLatin1Char('&'))));
    }

    if (InputSynth::modeOf(params) == InputSynth::Mode::Native) {
        const QRect rect = clickBar->tabRect(target);
        // A bar with more tabs than width scrolls, and a tab beyond the scrolled region has a
        // geometry the user cannot click. Saying so beats quietly switching behind their back.
        if (!clickBar->isVisible() || rect.isEmpty()
            || !clickBar->rect().contains(rect.center())) {
            throw CommandError(ErrorCode::NotActionable,
                               QStringLiteral("the tab '%1' is not clickable where the bar "
                                              "currently shows it; select_tab(mode='synthetic') "
                                              "reaches it anyway")
                                   .arg(clickBar->tabText(target).remove(QLatin1Char('&'))));
        }
        InputSynth::clickNative(clickBar, rect.center());
    } else if (bar) {
        bar->setCurrentIndex(target);
    } else {
        tabs->setCurrentIndex(target);
    }

    QVariantMap out;
    out.insert(QStringLiteral("index"), target);
    return out;
}

// ---------------------------------------------------------------- menus

QList<QAction *> WidgetBackend::menuPath(QObject *window, const QVariantMap &params,
                                         QMenuBar **barOut)
{
    auto *widget = qobject_cast<QWidget *>(window);
    if (!widget) {
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("menus are addressed from a widget window"));
    }
    QMenuBar *bar = widget->findChild<QMenuBar *>();
    if (!bar)
        throw CommandError(ErrorCode::NotFound, QStringLiteral("the window has no menu bar"));
    *barOut = bar;

    const QStringList path = params.value(QStringLiteral("path")).toString()
                                 .split(QLatin1Char('>'), Qt::SkipEmptyParts);
    if (path.isEmpty())
        throw CommandError(ErrorCode::InvalidParams, QStringLiteral("'path' is required"));

    QList<QAction *> chain;
    QList<QAction *> actions = bar->actions();
    QStringList walked;

    for (int depth = 0; depth < path.size(); ++depth) {
        const QString wanted = path.at(depth).trimmed();
        QAction *found = nullptr;
        for (QAction *action : actions) {
            QString label = action->text();
            if (label.remove(QLatin1Char('&')) == wanted) {
                found = action;
                break;
            }
        }
        if (!found) {
            // Listing the alternatives turns "not found" into something the reader can act on.
            QStringList available;
            for (QAction *action : actions) {
                QString label = action->text();
                label.remove(QLatin1Char('&'));
                if (!label.isEmpty())
                    available.append(label);
            }
            throw CommandError(ErrorCode::NotFound,
                               QStringLiteral("no menu entry '%1' under %2; there is: %3")
                                   .arg(wanted,
                                        walked.isEmpty() ? QStringLiteral("the menu bar")
                                                         : walked.join(QStringLiteral(" > ")),
                                        available.join(QStringLiteral(", "))));
        }
        walked.append(wanted);
        chain.append(found);
        if (depth + 1 < path.size()) {
            QMenu *submenu = found->menu();
            if (!submenu) {
                throw CommandError(ErrorCode::Unsupported,
                                   QStringLiteral("'%1' is not a submenu").arg(wanted));
            }
            actions = submenu->actions();
        }
    }
    return chain;
}

bool WidgetBackend::partPoint(QWidget *widget, const QString &part, QPoint *out)
{
    if (auto *spin = qobject_cast<QAbstractSpinBox *>(widget)) {
        QStyle::SubControl control = QStyle::SC_None;
        if (part == QLatin1String("spin_up"))
            control = QStyle::SC_SpinBoxUp;
        else if (part == QLatin1String("spin_down"))
            control = QStyle::SC_SpinBoxDown;
        if (control == QStyle::SC_None)
            return false;
        // The style decides where the arrows are, so the style is asked -- a hardcoded "right
        // edge, upper half" is wrong the moment a style stacks or mirrors them.
        QStyleOptionSpinBox option;
        option.initFrom(spin);
        option.subControls = QStyle::SC_All;
        const QRect rect = spin->style()->subControlRect(QStyle::CC_SpinBox, &option,
                                                         control, spin);
        if (rect.isEmpty())
            return false;
        *out = rect.center();
        return true;
    }
    return false;
}

QVariantList WidgetBackend::listProperties(QObject *object)
{
    const QMetaObject *meta = object->metaObject();
    QVariantList out;
    for (int i = 0; i < meta->propertyCount(); ++i) {
        const QMetaProperty property = meta->property(i);
        QVariantMap entry;
        entry.insert(QStringLiteral("name"), QString::fromUtf8(property.name()));
        entry.insert(QStringLiteral("type"), QString::fromUtf8(property.typeName()));
        entry.insert(QStringLiteral("writable"), property.isWritable());
        entry.insert(QStringLiteral("readable"), property.isReadable());
        // Which class introduced it, so a custom widget's own handful of properties can be told
        // apart from the sixty it inherits from QWidget.
        const QMetaObject *owner = meta;
        while (owner->superClass() && i < owner->propertyOffset())
            owner = owner->superClass();
        entry.insert(QStringLiteral("declared_in"), QString::fromUtf8(owner->className()));
        if (property.isReadable())
            entry.insert(QStringLiteral("value"), ValueCodec::encode(property.read(object)));
        out.append(entry);
    }
    return out;
}

} // namespace liberaqt
