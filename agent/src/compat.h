// Qt 5.15 / Qt 6.x compatibility shims.
//
// Rule: every #if QT_VERSION in the codebase lives in this file. Feature code stays readable.
#pragma once

#include <QtGlobal>
#include <QString>
#include <QVariant>
#include <QRegularExpression>

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

} // namespace liberaqt::compat
