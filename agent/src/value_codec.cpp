#include "value_codec.h"
#include "compat.h"

#include <QColor>
#include <QDateTime>
#include <QMetaEnum>
#include <QPoint>
#include <QRect>
#include <QSize>
#include <QUrl>
#include <QVariantList>
#include <QVariantMap>

namespace liberaqt {

QVariant ValueCodec::encode(const QVariant &value)
{
    if (!value.isValid())
        return {};

    switch (compat::variantTypeId(value)) {
    case QMetaType::Bool:
    case QMetaType::Int:
    case QMetaType::UInt:
    case QMetaType::LongLong:
    case QMetaType::ULongLong:
    case QMetaType::Double:
    case QMetaType::QString:
        return value;

    case QMetaType::QDateTime:
        return value.toDateTime().toString(Qt::ISODateWithMs);
    case QMetaType::QDate:
        return value.toDate().toString(Qt::ISODate);
    case QMetaType::QTime:
        return value.toTime().toString(Qt::ISODateWithMs);
    case QMetaType::QUrl:
        return value.toUrl().toString();
    case QMetaType::QColor:
        return value.value<QColor>().name(QColor::HexArgb);

    case QMetaType::QPoint: {
        const QPoint p = value.toPoint();
        return QVariantList{p.x(), p.y()};
    }
    case QMetaType::QSize: {
        const QSize s = value.toSize();
        return QVariantList{s.width(), s.height()};
    }
    case QMetaType::QRect: {
        const QRect r = value.toRect();
        return QVariantList{r.x(), r.y(), r.width(), r.height()};
    }

    case QMetaType::QVariantList: {
        QVariantList out;
        for (const QVariant &item : value.toList())
            out.append(encode(item));
        return out;
    }
    case QMetaType::QVariantMap: {
        QVariantMap out;
        const QVariantMap in = value.toMap();
        for (auto it = in.constBegin(); it != in.constEnd(); ++it)
            out.insert(it.key(), encode(it.value()));
        return out;
    }
    default:
        break;
    }

    // Enums travel as {"__enum": "QLineEdit::EchoMode", "value": 2}: the client compares against
    // plain ints, the type name stays available for diagnostics.
    if (compat::isEnumeration(value)) {
        QVariantMap enumMap;
        enumMap.insert(QStringLiteral("__enum"), compat::variantTypeName(value));
        enumMap.insert(QStringLiteral("value"), value.toInt());
        return enumMap;
    }

    QVariantMap opaque;
    opaque.insert(QStringLiteral("__opaque"), compat::variantTypeName(value));
    opaque.insert(QStringLiteral("repr"), value.toString());
    return opaque;
}

QVariant ValueCodec::decode(const QVariant &json)
{
    if (compat::variantTypeId(json) == QMetaType::QVariantMap) {
        const QVariantMap map = json.toMap();
        if (map.contains(QStringLiteral("__enum")))
            return map.value(QStringLiteral("value"));
        if (map.contains(QStringLiteral("__opaque")))
            return {}; // opaque values are read-only by design
    }
    return json;
}

} // namespace liberaqt
