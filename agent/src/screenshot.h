#pragma once

#include <QVariantMap>

namespace qtdriver {

class ObjectRegistry;

class Screenshot
{
public:
    // Grabs a widget, a window, or (with no handle) the active window. Returns base64 PNG.
    static QVariantMap grab(ObjectRegistry &registry, const QVariantMap &params);
};

} // namespace qtdriver
