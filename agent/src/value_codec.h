#pragma once

#include <QVariant>

namespace qtdriver {

// QVariant <-> JSON. Lossy conversions are made explicit with tagged objects rather than silently
// stringified, so a test never compares against a value that quietly changed shape on the wire.
// See docs/PROTOCOL.md section 3 for the full type table.
class ValueCodec
{
public:
    static QVariant encode(const QVariant &value);   // QVariant -> JSON-safe QVariant
    static QVariant decode(const QVariant &json);    // JSON-safe QVariant -> QVariant
};

} // namespace qtdriver
