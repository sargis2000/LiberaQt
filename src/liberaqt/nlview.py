# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Driving an NLview schematic canvas through its own command language.

NLview (Concept Engineering) is the schematic engine embedded in a great many EDA tools --
Libero SoC, ModelSim and QuestaSim all ship it in the same installation. It renders its scene
itself, so nothing inside it is a ``QObject``: no per-item objects, no accessibility children,
and no selector will ever reach an instance, a pin or a net. Measured on Libero, the canvas
reports itself as one opaque rectangle and its meta-object exposes no geometry at all.

What it does have is a command language, reachable through one exported method::

    const char *NlvQWidget::commandLine(bool *ok, const char *command)

That is not a slot, so :meth:`~liberaqt.locator.Locator.invoke` cannot reach it; this module
goes through :meth:`~liberaqt.locator.Locator.call_native` instead, with the caveats documented
there. Everything here is NLview's, not any particular vendor's -- ``NlvQWidget`` is NLview's own
Qt binding class and ``nlvqtb.dll`` its own library, so a canvas in any host is found the same
way and answers the same commands.

Example:
    Instances and their positions, then where one sits on screen::

        from liberaqt.nlview import NlviewCanvas

        canvas = NlviewCanvas.find(win)
        for inst in canvas.instances():
            print(inst.name, inst.x, inst.y, inst.pins)
        print(canvas.to_screen(inst.x, inst.y))
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import LiberaQtError, ObjectNotFoundError

if TYPE_CHECKING:
    from .locator import Locator

#: NLview's Qt binding class. The canvas in any host application derives from it, whatever the
#: host calls its own subclass -- Libero's is ``Aqnlvcanvas::NlvSDWidget``. Type matching walks
#: the inheritance chain, so this finds it regardless.
NLVIEW_CLASS = "NlvQWidget"

#: The library the binding lives in, and the exported entry point to the command language.
#: Mangled MSVC spelling, as ``dumpbin /exports`` reports it.
NLVIEW_MODULE = "nlvqtb.dll"
NLVIEW_COMMAND_SYMBOL = "?commandLine@NlvQWidget@@QAEPBDPA_NPBD@Z"


def parse_result(text: str) -> list[dict[str, Any]]:
    """Parse NLview's brace-list answer into one dict per object.

    Results come back Tcl-style: a space-separated run of ``{...}`` groups, each a few
    positional words followed by ``-option value...`` runs::

        {inst ahb2axi_inst_0 ahb2axi_inst::ahb2axi_inst_0 v -pg 1 -lvl 1 -x 20 -y 100}

    Option arity varies -- ``-x`` takes one value, ``-pinAttr HCLK @color #222222`` takes three
    -- so values are collected until the next option rather than by a fixed count. That keeps
    the parse lossless without needing to know every option NLview has.

    Args:
        text: Raw text as ``commandLine`` returned it.

    Returns:
        One dict per group, with ``words`` (the positional prefix) and ``options`` (a list of
        ``(name, [values])`` pairs, since options may repeat). Empty for an empty answer.
    """
    out: list[dict[str, Any]] = []
    for group in _brace_groups(text):
        words: list[str] = []
        options: list[tuple[str, list[str]]] = []
        for token in group.split():
            if token.startswith("-") and not _looks_numeric(token):
                options.append((token[1:], []))
            elif options:
                options[-1][1].append(token)
            else:
                words.append(token)
        out.append({"words": words, "options": options})
    return out


def _looks_numeric(token: str) -> bool:
    """Whether ``-12`` is a negative number rather than an option name."""
    try:
        float(token)
    except ValueError:
        return False
    return True


def _brace_groups(text: str) -> list[str]:
    """Top-level ``{...}`` groups, honouring nesting."""
    groups, depth, start = [], 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                groups.append(text[start:i])
                start = -1
    return groups


class NlviewObject:
    """One object NLview reported: an instance, a pin, a net or a port.

    Attributes:
        kind: What NLview called it -- ``inst``, ``pin``, ``net``, ``port``.
        name: Its name on the current page.
        full_name: The hierarchical name, when one was reported.
        options: Every ``-option`` NLview attached, as ``(name, [values])`` pairs.
    """

    def __init__(self, entry: dict[str, Any]):
        words = entry.get("words") or []
        self.kind = words[0] if words else ""
        self.name = words[1] if len(words) > 1 else ""
        self.full_name = words[2] if len(words) > 2 else ""
        self.options: list[tuple[str, list[str]]] = entry.get("options") or []

    def option(self, name: str) -> list[str] | None:
        """Values of the first ``-name`` option, or None when it was not reported."""
        for key, values in self.options:
            if key == name:
                return values
        return None

    @property
    def x(self) -> int | None:
        """Schematic x, present when the search asked for positions."""
        return self._int("x")

    @property
    def y(self) -> int | None:
        """Schematic y, present when the search asked for positions."""
        return self._int("y")

    @property
    def page(self) -> int | None:
        """Page the object is on."""
        return self._int("pg")

    @property
    def pins(self) -> list[str]:
        """Pin names, from the ``-pinAttr`` runs a long-form search reports.

        NLview repeats ``-pinAttr <pin> <attribute> <value>`` once per attribute, so a pin
        appears several times; order is preserved and duplicates dropped.
        """
        found: list[str] = []
        for key, values in self.options:
            if key == "pinAttr" and values and values[0] not in found:
                found.append(values[0])
        return found

    def _int(self, name: str) -> int | None:
        values = self.option(name)
        if not values:
            return None
        try:
            return int(values[0])
        except ValueError:
            return None

    def __repr__(self) -> str:
        where = f" at ({self.x},{self.y})" if self.x is not None else ""
        return f"<NlviewObject {self.kind} {self.name!r}{where}>"


class NlviewCanvas:
    """An NLview canvas, driven through its command language.

    Args:
        locator: A locator for the canvas widget. Must resolve to something deriving from
            ``NlvQWidget``, which :meth:`find` guarantees.
        module: Library exporting the command entry point.
        symbol: Exported name of ``NlvQWidget::commandLine``.
    """

    def __init__(self, locator: Locator, module: str = NLVIEW_MODULE,
                 symbol: str = NLVIEW_COMMAND_SYMBOL):
        self._locator = locator
        self._module = module
        self._symbol = symbol

    @classmethod
    def find(cls, window: Any, **kwargs: Any) -> NlviewCanvas:
        """Locate the NLview canvas in a window, whatever the host calls its subclass.

        Args:
            window: The window to search.
            **kwargs: Passed to the constructor.

        Returns:
            A canvas wrapper.

        Raises:
            ObjectNotFoundError: The window contains no NLview canvas.
        """
        found = window.locator(NLVIEW_CLASS)
        if not found.count:
            raise ObjectNotFoundError(
                f"no {NLVIEW_CLASS} in this window; is a schematic open?"
            )
        return cls(found.first, **kwargs)

    # ------------------------------------------------------------------ the command language

    def try_command(self, text: str) -> tuple[bool, str]:
        """Send a command and return ``(ok, output)`` without raising on failure.

        NLview answers a bad command by listing the ones it has, so a failure is often the most
        informative thing you can ask it -- ``try_command("help")`` prints the whole command set.

        Args:
            text: The command, e.g. ``"search -pos inst *"``.

        Returns:
            ``(ok, output)`` exactly as NLview reported them.
        """
        return self._locator.call_native(self._module, self._symbol, text)

    def command(self, text: str) -> str:
        """Send a command, raising when NLview rejects it.

        Args:
            text: The command.

        Returns:
            The output.

        Raises:
            LiberaQtError: NLview reported failure; the message is its own complaint, which
                usually names the correct syntax.
        """
        ok, output = self.try_command(text)
        if not ok:
            raise LiberaQtError(f"nlview rejected {text!r}: {output}")
        return output

    # ------------------------------------------------------------------ queries

    @property
    def version(self) -> str:
        """NLview's own version string."""
        return self.command("version")

    @property
    def module_name(self) -> str:
        """The module currently displayed, as ``"<name> <split-mode>"``."""
        return self.command("module")

    @property
    def page_size(self) -> tuple[int, int]:
        """Page size in schematic units."""
        parts = self.command("pagesize").split()
        return (int(parts[0]), int(parts[1])) if len(parts) >= 2 else (0, 0)

    def search(self, kind: str = "inst", pattern: str = "*",
               positions: bool = True, long: bool = False) -> list[NlviewObject]:
        """Find objects of a kind on the current page.

        Args:
            kind: ``inst``, ``pin``, ``net`` or ``port``.
            pattern: Glob to match names against.
            positions: Ask for ``-x``/``-y``, which is what makes :attr:`NlviewObject.x` useful.
            long: Ask for the long form, which is what carries ``-pinAttr`` and therefore
                :attr:`NlviewObject.pins`.

        Returns:
            One :class:`NlviewObject` per match, empty when nothing matched.
        """
        # Independent flags, and both are usually wanted: `-pos` carries -x/-y, `-long` carries
        # the -pinAttr runs the pin names come from. Asking for only one silently drops the
        # other, which reads as "this object has no position" rather than as a wrong question.
        flags = ""
        if positions:
            flags += " -pos"
        if long:
            flags += " -long"
        return [NlviewObject(e) for e in parse_result(
            self.command(f"search{flags} {kind} {pattern}")
        )]

    def instances(self, pattern: str = "*", long: bool = True) -> list[NlviewObject]:
        """Instances on the current page.

        Long form by default, because the pin names come with it.

        Args:
            pattern: Glob to match instance names against.
            long: Whether to ask for the long form.

        Returns:
            One :class:`NlviewObject` per instance.
        """
        return self.search("inst", pattern, long=long)

    # ------------------------------------------------------------------ coordinates

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Convert a schematic position to a screen position.

        The bridge between NLview's world and anything that clicks: schematic coordinates come
        back from :meth:`search`, and screen coordinates are what the mouse understands.

        Args:
            x: Schematic x.
            y: Schematic y.

        Returns:
            ``(x, y)`` in screen pixels.
        """
        return self._pair(self.command(f"convertcoords {int(x)} {int(y)}"))

    def to_schematic(self, x: int, y: int) -> tuple[int, int]:
        """Convert a screen position back to a schematic position.

        Args:
            x: Screen x.
            y: Screen y.

        Returns:
            ``(x, y)`` in schematic units.
        """
        return self._pair(self.command(f"convertcoords -reverse {int(x)} {int(y)}"))

    @staticmethod
    def _pair(text: str) -> tuple[int, int]:
        parts = text.split()
        if len(parts) < 2:
            raise LiberaQtError(f"expected two coordinates, got {text!r}")
        return int(parts[0]), int(parts[1])
