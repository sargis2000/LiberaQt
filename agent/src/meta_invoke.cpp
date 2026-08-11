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

namespace liberaqt {

namespace {

// QMetaMethod::invoke takes at most this many arguments.
constexpr int MaxArgs = 10;

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
    converted.reserve(args.size());
    typeNames.reserve(args.size());
    for (int i = 0; i < args.size(); ++i) {
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
    for (int i = 0; i < converted.size(); ++i)
        gen.append(QGenericArgument(typeNames.at(i).constData(), converted.at(i).constData()));
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
    const bool isVoid = returnType == QMetaType::Void || returnType == QMetaType::UnknownType;

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