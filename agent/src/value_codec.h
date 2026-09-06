// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Sargis Khachatryan
#pragma once

#include <QVariant>

namespace liberaqt {

// QVariant <-> JSON. Lossy conversions are made explicit with tagged objects rather than silently
// stringified, so a test never compares against a value that quietly changed shape on the wire.
// See docs/PROTOCOL.md section 3 for the full type table.
class ValueCodec
{
public:
    static QVariant encode(const QVariant &value);   // QVariant -> JSON-safe QVariant
    static QVariant decode(const QVariant &json);    // JSON-safe QVariant -> QVariant

    // Shape a decoded value to a known destination type (a property's or parameter's type).
    // JSON has no geometry types, so QPoint/QSize/QRect arrive as arrays and only become
    // themselves once something tells us what was expected. Returns the value unchanged when
    // no conversion applies, leaving the caller to report a precise failure.
    static QVariant coerce(const QVariant &value, int targetTypeId);
};

} // namespace liberaqt
