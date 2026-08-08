"""Session: owns the transport, the timeout policy and the object map.

Everything user-facing (Application, Window, Locator) delegates its wire access here, so there is
exactly one place that knows how to talk to an agent.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from .errors import LiberaQtError
from .protocol import Cmd
from .transport import Transport
from .waits import TimeoutPolicy


class ObjectMap:
    """Symbolic name to selector string, so a UI change is a one-line fix.

    Nested YAML is flattened with dots, so ``login: {submit: ...}`` is reached as
    ``"login.submit"``.

    Args:
        mapping: Nested or flat name-to-selector mapping. Empty when omitted.
    """

    def __init__(self, mapping: dict | None = None):
        self._flat = {}
        if mapping:
            self._flatten(mapping, prefix="")

    @classmethod
    def load(cls, path: str | None) -> ObjectMap:
        """Load an object map from a YAML file.

        Args:
            path: Path to the YAML file. ``None`` returns an empty map, which is the normal case
                for tests that use inline selectors.

        Returns:
            The loaded map.

        Raises:
            LiberaQtError: The file is missing, or PyYAML is not installed.
        """
        if not path:
            return cls()
        if not os.path.exists(path):
            raise LiberaQtError(f"object map not found: {path}")
        try:
            import yaml  # noqa: PLC0415 - optional dependency
        except ImportError as exc:
            raise LiberaQtError(
                "object maps need PyYAML", hint="pip install 'liberaqt[yaml]'"
            ) from exc
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    def _flatten(self, mapping: dict, prefix: str) -> None:
        for key, value in mapping.items():
            full = f"{prefix}{key}"
            if isinstance(value, dict):
                self._flatten(value, prefix=f"{full}.")
            else:
                self._flat[full] = value

    def resolve(self, name: str) -> str:
        """Look up the selector for a symbolic name.

        Args:
            name: Dotted name such as ``"login.submit"``.

        Returns:
            The selector string.

        Raises:
            LiberaQtError: No such entry. The hint lists names sharing the same first segment,
                which catches most typos.
        """
        try:
            return self._flat[name]
        except KeyError as exc:
            close = [k for k in self._flat if name.split(".")[0] in k][:5]
            raise LiberaQtError(
                f"'{name}' is not in the object map",
                hint=f"did you mean one of: {close}" if close else "check objects.yaml",
            ) from exc

    def __len__(self) -> int:
        return len(self._flat)

    def items(self):
        """Iterate over ``(dotted_name, selector)`` pairs.

        Returns:
            A view of the flattened entries.
        """
        return self._flat.items()


class Session:
    """The single place that knows how to talk to an agent.

    Holds the transport, the timeout policy and the object map, so the user-facing classes stay
    free of wire concerns.

    Args:
        transport: Connected transport to the agent.
        object_map: Symbolic names for selectors. Empty when omitted.
        default_timeout: Seconds actions wait by default.
        slowmo: Seconds to sleep before every command, for watching a test run.

    Attributes:
        idle_options: Defaults for :meth:`wait_for_idle`, adjustable per session for
            applications with a permanently running animation.
    """

    def __init__(self, transport: Transport, object_map: ObjectMap | None = None,
                 default_timeout: float = 5.0, slowmo: float = 0.0):
        self.transport = transport
        self.object_map = object_map or ObjectMap()
        self.timeouts = TimeoutPolicy(default_timeout)
        self.slowmo = slowmo
        self.idle_options = {"quiet_ms": 50, "animations": True, "network": False}

    def call(self, cmd: str, params: dict | None = None, timeout: float | None = None,
             selector: Any = None) -> Any:
        """Send a command and wait for its reply.

        The wire timeout is deliberately longer than the action timeout: the agent should be the
        one to report a timeout, with the context to explain it, rather than the client giving up
        first and leaving a reply in flight.

        Args:
            cmd: Protocol command name.
            params: Command parameters.
            timeout: Action timeout in seconds. Defaults to the session timeout.
            selector: Selector to attach to any error raised, for a better message.

        Returns:
            The command's result payload.

        Raises:
            LiberaQtError: The agent returned an error, translated to the matching subclass.
        """
        if self.slowmo:
            import time
            time.sleep(self.slowmo)
        return self.transport.call(cmd, params, timeout=self.timeouts.resolve(timeout) + 5.0,
                                   selector=selector)

    def wait_for_idle(self, quiet_ms: int | None = None, animations: bool | None = None,
                      network: bool | None = None, timeout: float | None = None) -> None:
        """Block until the UI settles.

        Each layer can be disabled independently, because most applications have one that does
        not apply, and a policy that cannot be relaxed is one that gets worked around with
        ``sleep()``.

        Args:
            quiet_ms: Milliseconds the event queue must stay empty.
            animations: Whether to wait for running animations to finish.
            network: Whether to wait for in-flight network replies. Opt-in.
            timeout: Seconds to wait. Defaults to the session timeout.
        """
        opts = dict(self.idle_options)
        if quiet_ms is not None:
            opts["quiet_ms"] = quiet_ms
        if animations is not None:
            opts["animations"] = animations
        if network is not None:
            opts["network"] = network
        self.call(Cmd.WAIT_IDLE, opts, timeout=timeout)

    def grab(self, handle: str | None = None, path: str | None = None) -> bytes:
        """Capture a PNG screenshot.

        Args:
            handle: Object or window to capture. ``None`` captures the whole application.
            path: Where to write the image. When omitted, the bytes are only returned.

        Returns:
            The PNG image bytes.
        """
        result = self.call(Cmd.GRAB, {"handle": handle})
        data = base64.b64decode(result.get("png", ""))
        if path:
            with open(path, "wb") as fh:
                fh.write(data)
        return data