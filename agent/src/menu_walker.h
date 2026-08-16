#pragma once

#include "dispatcher.h"

#include <QList>

class QAction;
class QComboBox;
class QMenuBar;

namespace liberaqt {

// Drives menus and combo boxes the way a user does: a real click opens each popup, and the next
// click lands inside it. Because native input is queued, every stage has to wait for the popup
// the previous click opened to actually be on screen before it can aim -- so these run as
// asynchronous commands, stepping from the event loop and resolving when the final click has
// been posted. Resolving then, rather than after it is processed, is what keeps a menu entry
// that opens a modal dialog from stranding the reply.
namespace menu_walker {

// Click the chain open: `chain[0]` is an entry on the bar, each next element an entry of the
// menu its predecessor opens, the last the one being activated.
void walkMenu(QMenuBar *bar, const QList<QAction *> &chain,
              Dispatcher::Resolver resolve, Dispatcher::Rejecter reject);

// Click the combo open, then click row `index` in its popup view.
void selectComboEntry(QComboBox *combo, int index,
                      Dispatcher::Resolver resolve, Dispatcher::Rejecter reject);

} // namespace menu_walker

} // namespace liberaqt
