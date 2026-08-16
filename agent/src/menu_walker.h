#pragma once

#include "dispatcher.h"

#include <QPoint>
#include <QStringList>

class QComboBox;
class QMenuBar;
class QWidget;

namespace liberaqt {

// Drives menus and combo boxes the way a user does: a real click opens each popup, and the next
// click lands inside it. Because native input is queued, every stage has to wait for the popup
// the previous click opened to actually be on screen before it can aim -- so these run as
// asynchronous commands, stepping from the event loop and resolving when the final click has
// been posted. Resolving then, rather than after it is processed, is what keeps a menu entry
// that opens a modal dialog from stranding the reply.
namespace menu_walker {

// Click a "File > Export > PDF..." path open, resolving each level only AFTER the click has
// genuinely opened its menu. That ordering is the point: menus routinely create their entries
// in aboutToShow -- "Recent Files" lists -- so an entry can be unresolvable until its menu is
// on screen. Resolves with the activated entry's {text, enabled, checked, clicked}.
void walkMenu(QMenuBar *bar, const QStringList &path,
              Dispatcher::Resolver resolve, Dispatcher::Rejecter reject);

// Click the combo open, then click row `index` in its popup view.
void selectComboEntry(QComboBox *combo, int index,
                      Dispatcher::Resolver resolve, Dispatcher::Rejecter reject);

// Right-click `point` inside `target`, wait for the context menu, and walk `path` inside it by
// clicking -- Squish's openItemContextMenu + activateItem in one move. Native-only by nature: a
// context menu is *built* inside contextMenuEvent, so there is no QAction to trigger without
// genuinely opening it. A wrong entry name fails listing what the menu really offers, which
// doubles as the way to discover an unfamiliar application's context menus.
void walkContextMenu(QWidget *target, const QPoint &point, const QStringList &path,
                     Dispatcher::Resolver resolve, Dispatcher::Rejecter reject);

} // namespace menu_walker

} // namespace liberaqt
