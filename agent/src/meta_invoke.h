// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
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

    // Everything the meta-object knows how to call, so `invoke` stops being a guessing game.
    //
    // The companion to object.list_properties: between them they are the whole surface a
    // QObject offers without its headers. That matters most for a widget that paints its own
    // contents -- a schematic canvas, a chart, a custom editor -- where nothing is reachable
    // through the object tree and the meta-object is the only way in.
    //
    // Signals are listed but marked not callable: invoking one fakes an event the application
    // never had, which is a way to make a test lie rather than a way to drive anything.
    static QVariantList listMethods(QObject *object);

    // Calls an *exported* C++ method on an object, by symbol, for methods moc cannot see.
    //
    // A deliberately sharp tool and the last resort. Plain public methods are invisible to the
    // meta-object system, so a library can expose its entire API and none of it be reachable:
    // NLview's `NlvQWidget::commandLine` is the entry point to its whole command language, is
    // exported from nlvqtb.dll, and is not a slot.
    //
    // Two things keep it from being reckless. Only a module the application has *already loaded*
    // is used, never one the agent loads itself -- loading a library would run its initialisation
    // inside the application under test, which a driver has no business doing. And the call sits
    // behind a structured-exception guard where the compiler provides one, so a wrong signature
    // returns an error instead of taking the application down with it.
    //
    // That guard is a safety net, not a licence. The signature still has to be right, and the
    // object still has to be an instance of the class that declared the method -- check with
    // a selector before calling, since type matching walks the inheritance chain.
    //
    // `signature` names one supported shape rather than accepting a free-form spelling, so the
    // set of ways to get this wrong stays small and enumerable.
    static QVariant callNative(QObject *object, const QString &module, const QString &symbol,
                               const QString &signature, const QVariantList &args);
};

} // namespace liberaqt