// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#include "meta_invoke.h"

#include "compat.h"
#include "dispatcher.h"
#include "value_codec.h"

#include <QByteArray>
#include <QList>
#include <QMetaMethod>
#include <QMetaObject>
#include <QStringList>
#include <QVarLengthArray>

#include <array>
#include <string>

#ifdef Q_OS_WIN
#  include <windows.h>
#endif

namespace liberaqt {

namespace {

// QMetaMethod::invoke takes at most this many arguments.
constexpr int MaxArgs = 10;

// The declared type with references and const stripped, so `const std::string&` and
// `std::string` are recognised as the same thing.
QByteArray bareTypeName(QByteArray name)
{
    if (name.startsWith("const "))
        name = name.mid(6);
    while (name.endsWith('&') || name.endsWith(' '))
        name.chop(1);
    return name.trimmed();
}

bool isCallable(const QMetaMethod &method)
{
    // Signals are invokable too, but calling one from a test would fake an event the application
    // never produced, so they are deliberately excluded.
    return method.methodType() == QMetaMethod::Slot
           || method.methodType() == QMetaMethod::Method;
}

QStringList invokableSignatures(const QMetaObject *mo)
{
    QStringList out;
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod method = mo->method(i);
        if (isCallable(method))
            out.append(QString::fromUtf8(method.methodSignature()));
    }
    return out;
}

} // namespace

namespace {

// const char *(__thiscall *)(void *self, bool *ok, const char *arg)
//
// __thiscall matters on 32-bit MSVC: `this` travels in ECX rather than on the stack, so a plainly
// cast pointer would put every argument in the wrong place and corrupt the call. x86-64 has a
// single convention, so the distinction disappears there.
#if defined(_MSC_VER) && defined(_M_IX86)
using CStrBoolCStrFn = const char *(__thiscall *)(void *, bool *, const char *);
#else
using CStrBoolCStrFn = const char *(*)(void *, bool *, const char *);
#endif

//: The one shape supported so far, named rather than parsed.
const char SigCStrBoolCStr[] = "cstr(bool*,cstr)";

#if defined(_MSC_VER) && defined(Q_OS_WIN)
// The guarded call has to live in a function of its own: MSVC refuses __try in any function that
// holds objects needing unwinding (C2712), and the caller is full of QStrings. Everything here is
// a pointer or a bool, so there is nothing to unwind.
bool guardedCall(CStrBoolCStrFn fn, void *self, bool *ok, const char *arg, const char **result)
{
    __try {
        *result = fn(self, ok, arg);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
#endif

} // namespace

QVariant MetaInvoke::callNative(QObject *object, const QString &module, const QString &symbol,
                                const QString &signature, const QVariantList &args)
{
#ifndef Q_OS_WIN
    Q_UNUSED(object);
    Q_UNUSED(module);
    Q_UNUSED(symbol);
    Q_UNUSED(signature);
    Q_UNUSED(args);
    throw CommandError(ErrorCode::Unsupported,
                       QStringLiteral("object.call_native is implemented for Windows only"));
#else
    if (signature != QLatin1String(SigCStrBoolCStr)) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("unsupported signature '%1'; the only shape so far is "
                                          "'%2'").arg(signature,
                                                      QLatin1String(SigCStrBoolCStr)));
    }
    if (args.size() != 1) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("'%1' takes exactly one string argument, got %2")
                               .arg(QLatin1String(SigCStrBoolCStr)).arg(args.size()));
    }

    // Already loaded only: the agent never loads a library into the application itself.
    HMODULE handle = GetModuleHandleW(reinterpret_cast<const wchar_t *>(module.utf16()));
    if (!handle) {
        QVariantMap data;
        data.insert(QStringLiteral("hint"),
                    QStringLiteral("the application must already be using it; the agent will not "
                                   "load a library on its behalf"));
        throw CommandError(ErrorCode::NotFound,
                           QStringLiteral("module '%1' is not loaded in this process").arg(module),
                           data);
    }

    const QByteArray name = symbol.toLatin1();
    FARPROC address = GetProcAddress(handle, name.constData());
    if (!address) {
        QVariantMap data;
        data.insert(QStringLiteral("hint"),
                    QStringLiteral("C++ names are mangled; pass the exact exported spelling, as "
                                   "`dumpbin /exports` reports it"));
        throw CommandError(ErrorCode::NotFound,
                           QStringLiteral("'%1' exports no symbol '%2'").arg(module, symbol),
                           data);
    }

    const QByteArray argument = args.at(0).toString().toUtf8();
    auto fn = reinterpret_cast<CStrBoolCStrFn>(reinterpret_cast<void *>(address));
    bool ok = false;
    const char *result = nullptr;
    bool faulted = false;

#ifdef _MSC_VER
    // The safety net: a wrong signature, or an object that is not an instance of the declaring
    // class, faults here. Turning that into a reply is the difference between a failed command
    // and a lost session -- the agent lives inside the application under test.
    faulted = !guardedCall(fn, object, &ok, argument.constData(), &result);
#else
    result = fn(object, &ok, argument.constData());
#endif

    if (faulted) {
        throw CommandError(ErrorCode::Internal,
                           QStringLiteral("calling '%1' faulted: the signature is wrong, or the "
                                          "object is not an instance of the class that declares "
                                          "it").arg(symbol));
    }

    QVariantMap out;
    out.insert(QStringLiteral("ok"), ok);
    out.insert(QStringLiteral("value"), result ? QString::fromUtf8(result) : QString());
    return out;
#endif
}

QVariantList MetaInvoke::listMethods(QObject *object)
{
    const QMetaObject *meta = object->metaObject();
    QVariantList out;
    for (int i = 0; i < meta->methodCount(); ++i) {
        const QMetaMethod method = meta->method(i);
        QVariantMap entry;
        entry.insert(QStringLiteral("name"), QString::fromUtf8(method.name()));
        entry.insert(QStringLiteral("signature"), QString::fromUtf8(method.methodSignature()));

        QString kind;
        switch (method.methodType()) {
        case QMetaMethod::Signal:      kind = QStringLiteral("signal"); break;
        case QMetaMethod::Slot:        kind = QStringLiteral("slot"); break;
        case QMetaMethod::Constructor: kind = QStringLiteral("constructor"); break;
        default:                       kind = QStringLiteral("method"); break;   // Q_INVOKABLE
        }
        entry.insert(QStringLiteral("kind"), kind);
        entry.insert(QStringLiteral("callable"), isCallable(method));
        entry.insert(QStringLiteral("return_type"), QString::fromUtf8(method.typeName()));

        QVariantList parameters;
        const QList<QByteArray> types = method.parameterTypes();
        const QList<QByteArray> names = method.parameterNames();
        for (int p = 0; p < types.size(); ++p) {
            QVariantMap arg;
            arg.insert(QStringLiteral("type"), QString::fromUtf8(types.at(p)));
            arg.insert(QStringLiteral("name"), QString::fromUtf8(names.value(p)));
            parameters.append(arg);
        }
        entry.insert(QStringLiteral("parameters"), parameters);

        // Which class introduced it, so a custom widget's own handful can be told apart from the
        // hundred it inherits from QWidget.
        const QMetaObject *owner = meta;
        while (owner->superClass() && i < owner->methodOffset())
            owner = owner->superClass();
        entry.insert(QStringLiteral("declared_in"), QString::fromUtf8(owner->className()));

        out.append(entry);
    }
    return out;
}

QVariant MetaInvoke::call(QObject *object, const QString &name, const QVariantList &args,
                          bool queued)
{
    if (args.size() > MaxArgs) {
        throw CommandError(ErrorCode::InvalidParams,
                           QStringLiteral("at most %1 arguments are supported, got %2")
                               .arg(MaxArgs).arg(args.size()));
    }

    const QMetaObject *mo = object->metaObject();
    const QString className = QString::fromUtf8(mo->className());
    const QByteArray wanted = name.toUtf8();

    QMetaMethod method;
    QStringList sameName;
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod candidate = mo->method(i);
        if (candidate.name() != wanted || !isCallable(candidate))
            continue;
        sameName.append(QString::fromUtf8(candidate.methodSignature()));
        if (candidate.parameterCount() == args.size()) {
            method = candidate;
            break;
        }
    }

    if (!method.isValid()) {
        QVariantMap data;
        data.insert(QStringLiteral("class"), className);
        if (!sameName.isEmpty()) {
            // The name exists; the caller just got the arity wrong. Show the real overloads.
            data.insert(QStringLiteral("overloads"), sameName);
            throw CommandError(ErrorCode::InvalidParams,
                               QStringLiteral("%1 has no '%2' taking %3 argument(s)")
                                   .arg(className, name).arg(args.size()),
                               data);
        }
        data.insert(QStringLiteral("invokable"), invokableSignatures(mo));
        throw CommandError(ErrorCode::Unsupported,
                           QStringLiteral("%1 has no invokable method '%2' -- only slots and "
                                          "Q_INVOKABLE methods can be called")
                               .arg(className, name),
                           data);
    }

    // Convert every argument up front. The QGenericArgument values built below hold bare
    // pointers into these containers, so both must be fully populated -- and never resized
    // again -- before any pointer into them is taken.
    QVariantList converted;
    QList<QByteArray> typeNames;
    // Stable storage for std::string parameters. Fixed-size on purpose: the QGenericArgument
    // values below hold bare pointers into it, and anything that could reallocate would leave
    // them dangling.
    std::array<std::string, MaxArgs> stdStrings;
    std::array<bool, MaxArgs> viaStdString{};
    converted.reserve(args.size());
    typeNames.reserve(args.size());
    for (int i = 0; i < args.size(); ++i) {
        // std::string is not a registered metatype, so QVariant cannot convert into it and the
        // whole parameter would be rejected. A *direct* invocation never needs the metatype
        // though -- it only forwards a pointer -- so building the value here is enough.
        //
        // This matters more than it sounds: an application written in ordinary C++ exposes half
        // its slots this way, and without it every one of them is unreachable. Libero's
        // schematic canvas keeps its entire pin and net query API behind std::string arguments
        // (isNetHidden, isPinHidden, doHideNet, doExpandInPlace).
        //
        // Safe only because the agent is already built with the same compiler and standard
        // library as the application -- a Qt plugin that was not could never have loaded.
        if (bareTypeName(compat::parameterTypeName(method, i)) == "std::string") {
            const QVariant decoded = ValueCodec::decode(args.at(i));
            if (!decoded.canConvert<QString>()) {
                throw CommandError(ErrorCode::InvalidParams,
                                   QStringLiteral("argument %1 of '%2' must be a string")
                                       .arg(i).arg(name));
            }
            stdStrings[i] = decoded.toString().toStdString();
            viaStdString[i] = true;
            converted.append(QVariant());
            typeNames.append(compat::parameterTypeName(method, i));
            continue;
        }

        const int want = method.parameterType(i);
        QVariant value = ValueCodec::coerce(ValueCodec::decode(args.at(i)), want);
        if (compat::variantTypeId(value) != want) {
            throw CommandError(ErrorCode::InvalidParams,
                               QStringLiteral("argument %1 of '%2' must be %3")
                                   .arg(i).arg(name,
                                        QString::fromUtf8(compat::parameterTypeName(method, i))));
        }
        converted.append(value);
        typeNames.append(compat::parameterTypeName(method, i));
    }

    QVarLengthArray<QGenericArgument, MaxArgs> gen;
    for (int i = 0; i < converted.size(); ++i) {
        const void *data = viaStdString[i] ? static_cast<const void *>(&stdStrings[i])
                                           : converted.at(i).constData();
        gen.append(QGenericArgument(typeNames.at(i).constData(), data));
    }
    while (gen.size() < MaxArgs)
        gen.append(QGenericArgument());

    // Posting the call rather than making it. The handler returns at once, so a method that
    // opens a modal dialog no longer holds its own reply hostage to the nested event loop that
    // dialog runs. Nothing can be reported back about the outcome, hence the explicit opt-in.
    if (queued) {
        const bool ok = method.invoke(object, Qt::QueuedConnection,
                                      gen[0], gen[1], gen[2], gen[3], gen[4],
                                      gen[5], gen[6], gen[7], gen[8], gen[9]);
        if (!ok) {
            throw CommandError(ErrorCode::Internal,
                               QStringLiteral("queueing %1::%2 failed").arg(className, name));
        }
        QVariantMap queuedOut;
        queuedOut.insert(QStringLiteral("queued"), true);
        queuedOut.insert(QStringLiteral("value"), QVariant());
        return queuedOut;
    }

    // Already marshalled onto the GUI thread by the dispatcher, so a direct call is correct
    // and keeps the return value usable.
    const int returnType = method.returnType();
    // A std::string return is unknown to the metatype system for the same reason, and would
    // otherwise be mistaken for void and silently dropped.
    const bool returnsStdString = bareTypeName(method.typeName()) == "std::string";
    const bool isVoid = !returnsStdString
                        && (returnType == QMetaType::Void || returnType == QMetaType::UnknownType);

    QVariantMap out;
    if (isVoid) {
        const bool ok = method.invoke(object, Qt::DirectConnection,
                                      gen[0], gen[1], gen[2], gen[3], gen[4],
                                      gen[5], gen[6], gen[7], gen[8], gen[9]);
        if (!ok) {
            throw CommandError(ErrorCode::Internal,
                               QStringLiteral("invoking %1::%2 failed").arg(className, name));
        }
        out.insert(QStringLiteral("value"), QVariant());
        return out;
    }

    if (returnsStdString) {
        std::string text;
        QGenericReturnArgument ret(method.typeName(), &text);
        const bool ok = method.invoke(object, Qt::DirectConnection, ret,
                                      gen[0], gen[1], gen[2], gen[3], gen[4],
                                      gen[5], gen[6], gen[7], gen[8], gen[9]);
        if (!ok) {
            throw CommandError(ErrorCode::Internal,
                               QStringLiteral("invoking %1::%2 failed").arg(className, name));
        }
        out.insert(QStringLiteral("value"), QString::fromStdString(text));
        return out;
    }

    QVariant result = compat::variantOfType(returnType);
    QGenericReturnArgument ret(method.typeName(), result.data());
    const bool ok = method.invoke(object, Qt::DirectConnection, ret,
                                  gen[0], gen[1], gen[2], gen[3], gen[4],
                                  gen[5], gen[6], gen[7], gen[8], gen[9]);
    if (!ok) {
        throw CommandError(ErrorCode::Internal,
                           QStringLiteral("invoking %1::%2 failed").arg(className, name));
    }
    out.insert(QStringLiteral("value"), ValueCodec::encode(result));
    return out;
}

} // namespace liberaqt