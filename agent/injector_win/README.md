# Windows DLL injection (attach mode)

Used only for attaching to an already-running process. Launch mode uses the generic-plugin
mechanism instead, which needs no injection at all.

Mechanism: `OpenProcess` -> `VirtualAllocEx` -> `WriteProcessMemory` (the agent DLL path) ->
`CreateRemoteThread` on `LoadLibraryW`.

Caveats to document loudly for users:

* The injector and the target must have the same bitness.
* Endpoint protection software flags `CreateRemoteThread` as malicious behaviour. Expect to
  allowlist the injector, and expect this to be the #1 support question.
* Requires `SeDebugPrivilege` for processes running under a different user.

Prefer launch mode, or the in-app embed API (`agent/src/embed.h`), for anything that has to run on
a locked-down corporate machine.

Not implemented in the skeleton. Milestone 5.
