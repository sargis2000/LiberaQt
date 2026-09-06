// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
// Qt 5.15 / Qt 6.x compatibility shims.
//
// Rule: every #if QT_VERSION in the codebase lives in this file. Feature code stays readable.
#pragma once

#include <QtGlobal>
// QMetaProperty lives here; <QMetaObject> forwards to qobjectdefs.h, which only declares it.
#include <QtCore/qmetaobject.h>
#include <QKeySequence>
#include <QString>
#include <QVariant>
#include <QRegularExpression>
#include <QWindow>

// The one private header the agent uses. QWindowSystemInterface is the seam a platform plugin
// pushes real input through, so injecting here is the difference between "Qt behaved as if the
// user did it" and "a widget's handler was called directly". Isolated in this file by the same
// rule that isolates the version checks.
#include <qpa/qwindowsysteminterface.h>

#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
#  include <QMetaType>
#  define LIBERAQT_QT6 1
#else
#  define LIBERAQT_QT6 0
#endif

namespace liberaqt::compat {

inline int variantTypeId(const QVariant &v)
{
#if LIBERAQT_QT6
    return v.metaType().id();
#else
    return static_cast<int>(v.type());
#endif
}

inline QString variantTypeName(const QVariant &v)
{
#if LIBERAQT_QT6
    return QString::fromUtf8(v.metaType().name());
#else
    return QString::fromUtf8(v.typeName() ? v.typeName() : "");
#endif
}

inline bool isEnumeration(const QVariant &v)
{
#if LIBERAQT_QT6
    return v.metaType().flags().testFlag(QMetaType::IsEnumeration);
#else
    return QMetaType::typeFlags(v.userType()).testFlag(QMetaType::IsEnumeration);
#endif
}

inline int propertyTypeId(const QMetaProperty &property)
{
#if LIBERAQT_QT6
    return property.metaType().id();
#else
    return property.userType();
#endif
}

// Default-constructed QVariant of a given metatype id, used to hold an invoked method's
// return value before it is encoded.
inline QVariant variantOfType(int typeId)
{
#if LIBERAQT_QT6
    return QVariant(QMetaType(typeId), nullptr);
#else
    return QVariant(typeId, nullptr);
#endif
}

// Qt 6 removed the implicit QString -> QVariant metatype lookups used by invokeMethod helpers.
inline bool canConvert(const QVariant &v, int typeId)
{
#if LIBERAQT_QT6
    return v.canConvert(QMetaType(typeId));
#else
    return v.canConvert(typeId);
#endif
}

inline bool convert(QVariant &v, int typeId)
{
#if LIBERAQT_QT6
    return v.convert(QMetaType(typeId));
#else
    return v.convert(typeId);
#endif
}

// QMetaMethod::parameterTypeName() arrived in Qt 6. Qt 5 only offers the whole list at once,
// which allocates, so callers that need several should hoist parameterTypes() themselves.
inline QByteArray parameterTypeName(const QMetaMethod &method, int index)
{
#if LIBERAQT_QT6
    return method.parameterTypeName(index);
#else
    const QList<QByteArray> types = method.parameterTypes();
    return index >= 0 && index < types.size() ? types.at(index) : QByteArray();
#endif
}

// ---- key sequences -------------------------------------------------------------------------

// The first chord of a key sequence, split into a key code and its modifiers. Qt 6 models a chord
// as QKeyCombination; Qt 5 packs both into one int.
inline void firstChord(const QKeySequence &sequence, int *keyCode, Qt::KeyboardModifiers *mods)
{
#if LIBERAQT_QT6
    const QKeyCombination combo = sequence[0];
    *keyCode = combo.key();
    *mods = combo.keyboardModifiers();
#else
    const int raw = sequence[0];
    *keyCode = raw & ~Qt::KeyboardModifierMask;
    *mods = Qt::KeyboardModifiers(raw & Qt::KeyboardModifierMask);
#endif
}

// ---- window-system input -------------------------------------------------------------------
//
// These queue an event and return; the platform event dispatcher drains the queue on the next
// pass through the event loop. Queuing rather than delivering is deliberate. Synchronous delivery
// would not return until any modal dialog the input opened had closed, which is the one failure
// mode this codebase keeps rediscovering -- so every caller here is an asynchronous command that
// resolves a turn later, once the queue has been drained.
//
// The signatures below are identical on Qt 5.15 and Qt 6, hence no version guards; they are
// wrapped anyway so that the private include stays in this file.

inline void postMouse(QWindow *window, const QPointF &local, const QPointF &global,
                      Qt::MouseButtons buttons, Qt::MouseButton button, QEvent::Type type,
                      Qt::KeyboardModifiers mods)
{
    QWindowSystemInterface::handleMouseEvent(window, local, global, buttons, button, type, mods);
}

inline void postKey(QWindow *window, QEvent::Type type, int key, Qt::KeyboardModifiers mods,
                    const QString &text)
{
    QWindowSystemInterface::handleKeyEvent(window, type, key, mods, text);
}

// The pointer crossing into a window, which the platform reports as its own event rather than
// deriving it from a move. Some widgets refuse input until they have seen one: QMenu guards its
// whole mousePressEvent behind `hasReceievedEnter`, so an injected click on a menu that was
// never entered is silently discarded -- in both delivery modes, since neither generates it.
inline void postEnter(QWindow *window, const QPointF &local, const QPointF &global)
{
    QWindowSystemInterface::handleEnterEvent(window, local, global);
}

inline void postWheel(QWindow *window, const QPointF &local, const QPointF &global,
                      QPoint pixelDelta, QPoint angleDelta, Qt::KeyboardModifiers mods)
{
    QWindowSystemInterface::handleWheelEvent(window, local, global, pixelDelta, angleDelta, mods);
}

// What the platform reports when the user brings a window to the front. It is the only way an
// injected click can leave the application genuinely active rather than merely poked.
//
// The call was renamed *within* Qt 6, not at the 5-to-6 boundary as this once assumed -- which is
// what a Qt 6.5 build discovered, since gating on LIBERAQT_QT6 sent it to a function that does not
// exist there. Verified by reading the shipped headers: 5.15 and 6.5.3 declare
// handleWindowActivated, 6.7.3 declares handleFocusWindowChanged.
//
// The boundary is set at 6.6, where Qt introduced the new name. 6.6 itself is untested here --
// only 6.5 and 6.7 were available -- but a wrong boundary fails loudly at compile time with
// "is not a member of QWindowSystemInterface", not silently at run time.
inline void postWindowActivated(QWindow *window)
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
    QWindowSystemInterface::handleFocusWindowChanged(window, Qt::ActiveWindowFocusReason);
#else
    QWindowSystemInterface::handleWindowActivated(window, Qt::ActiveWindowFocusReason);
#endif
}

} // namespace liberaqt::compat
