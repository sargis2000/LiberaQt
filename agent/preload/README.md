# LD_PRELOAD fallback (Linux)

Used when the generic-plugin mechanism does not apply: the app clears `QT_QPA_GENERIC_PLUGINS`,
ships a `qt.conf` that pins `QT_PLUGIN_PATH`, or uses a custom plugin loader.

```
LD_PRELOAD=libliberaqt_preload.so ./myapp
```

`liberaqt_preload.cpp` has a library constructor that `dlopen`s the real agent and hooks
`QCoreApplication`'s constructor via `dlsym(RTLD_NEXT, ...)`. Same ABI constraint as the plugin:
build against the same Qt and compiler as the AUT.

Not implemented in the skeleton. Milestone 3.
