"""The Claude Code material in this repository cannot drift from the library it describes.

``plugins/liberaqt/`` ships to LiberaQT users and ``.claude/`` guides this repository's own
sessions. Both quote the API in code blocks and in prose, and the reader is a model: a method that
was renamed will not look wrong to it, it will be called with confidence. So every Python block is
parsed, every import is resolved, and every attribute used on a receiver whose type is known --
``app.``, ``win.``, ``loc.``, ``expect(...)``, or a variable assigned from one of those -- is
checked against the real class.

Negative claims are checked too. The material says there is no ``win.app``; if one is ever added,
:data:`ABSENT` fails and points at the sentence that has become false.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

import liberaqt
from liberaqt import Application, LiberaQt, Locator, Window
from liberaqt.keyboard import Keyboard
from liberaqt.mouse import Mouse
from liberaqt.window import MenuAction

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "plugins"
DEV_ROOT = ROOT / ".claude"
MARKDOWN = sorted([*PLUGIN_ROOT.rglob("*.md"), *DEV_ROOT.rglob("*.md")])

_expect = sys.modules["liberaqt.expect"]
ASSERTIONS = (_expect.LocatorAssertions, _expect.WindowAssertions)

#: What a bare name means when a snippet uses it without assigning it first.
CONVENTIONAL = {
    "lq": (LiberaQt,),
    "app": (Application,),
    "app_session": (Application,),
    "child": (Application,),
    "win": (Window,),
    "dlg": (Window,),
    "loc": (Locator,),
    "view": (Locator,),
    "table": (Locator,),
    "tree": (Locator,),
    "field": (Locator,),
    "LiberaQt": (LiberaQt,),
    "Application": (Application,),
    "Window": (Window,),
    "Locator": (Locator,),
    "Keyboard": (Keyboard,),
    "Mouse": (Mouse,),
}

#: Fixture parameters, bound only inside a function that takes them. ``liberaqt`` is kept out of
#: CONVENTIONAL because in prose it is the package, or ``liberaqt.toml``, not the driver.
FIXTURES = {"liberaqt": (LiberaQt,), "app": (Application,), "app_session": (Application,)}

#: Calls whose result has a known type, by the method that produced it.
RETURNS = {
    "launch": (Application,),
    "connect": (Application,),
    "attach_child": (Application,),
    "window": (Window,),
    "wait_for_window": (Window,),
    "locator": (Locator,),
    "obj": (Locator,),
    "nth": (Locator,),
    "filter": (Locator,),
    "child": (Locator,),
    "parent": (Locator,),
    "ancestor": (Locator,),
    "row": (Locator,),
    "cell": (Locator,),
    "item": (Locator,),
    "menu": (MenuAction,),
}

#: Properties whose value has a known type.
PROPERTIES = {
    "first": (Locator,),
    "last": (Locator,),
    "keyboard": (Keyboard,),
    "mouse": (Mouse,),
    "not_": (_expect.LocatorAssertions,),
}

#: Attributes the material says do not exist. Each must stay absent, or the material is wrong.
ABSENT = {
    (Window, "app"): "write-tests and its api.md say there is no win.app",
    (_expect.WindowAssertions, "not_"): "write-tests and limitations.md say it has no not_",
    (_expect.WindowAssertions, "to_be_visible"): "limitations.md says expect(win) cannot do this",
}


def _has(classes: tuple[type, ...], attr: str) -> bool:
    for cls in classes:
        if hasattr(cls, attr):
            return True
        # Instance attributes, such as Application._session, are set in a constructor.
        if attr.startswith("_") and re.search(rf"self\.{attr}\b", inspect.getsource(cls)):
            return True
    return False


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"^```python[^\n]*\n(.*?)^```", text, re.M | re.S)


def _frontmatter(path: Path) -> dict:
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path.relative_to(ROOT)} has no YAML frontmatter"
    return yaml.safe_load(match.group(1))


class _Checker(ast.NodeVisitor):
    """Walk a snippet in order, inferring receiver types and recording unknown attributes."""

    def __init__(self) -> None:
        self.env: dict[str, tuple[type, ...]] = {}
        self.problems: list[str] = []

    def kind(self, node: ast.AST) -> tuple[type, ...] | None:
        if isinstance(node, ast.Name):
            return self.env.get(node.id) or CONVENTIONAL.get(node.id)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "expect":
                return ASSERTIONS
            if isinstance(func, ast.Name) and func.id in ("liberaqt", "LiberaQt"):
                return (LiberaQt,)
            if isinstance(func, ast.Name) and func.id == "next" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.GeneratorExp):
                    return self.kind(arg.elt)
            if isinstance(func, ast.Attribute) and self.kind(func.value):
                return RETURNS.get(func.attr)
        if isinstance(node, ast.Attribute) and self.kind(node.value):
            return PROPERTIES.get(node.attr)
        return None

    def _bind(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name) and (kind := self.kind(value)):
            self.env[target.id] = kind

    def _bind_iteration(self, target: ast.AST, iterable: ast.AST) -> None:
        # ``for x in loc.all()`` and ``(x for x in loc.all())`` give locators.
        if (isinstance(target, ast.Name) and isinstance(iterable, ast.Call)
                and isinstance(iterable.func, ast.Attribute) and iterable.func.attr == "all"
                and self.kind(iterable.func.value)):
            self.env[target.id] = (Locator,)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for arg in node.args.args:
            if arg.arg in FIXTURES:
                self.env[arg.arg] = FIXTURES[arg.arg]
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._bind(target, node.value)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind(item.optional_vars, item.context_expr)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bind_iteration(node.target, node.iter)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._bind_iteration(node.target, node.iter)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        # Bind the loop variable before the element that uses it is visited.
        for generator in node.generators:
            self.visit(generator)
        self.visit(node.elt)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        kind = self.kind(node.value)
        if kind and not node.attr.startswith("__") and not _has(kind, node.attr):
            names = "/".join(c.__name__ for c in kind)
            self.problems.append(f"{names} has no attribute {node.attr!r}")
        self.generic_visit(node)


# ------------------------------------------------------------------ the manifests


def test_the_marketplace_lists_a_plugin_that_exists():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    assert marketplace["plugins"], "the marketplace lists no plugin"
    for entry in marketplace["plugins"]:
        source = ROOT / entry["source"]
        manifest = json.loads((source / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        assert manifest["name"] == entry["name"]


def test_the_plugin_describes_the_release_line_of_this_library():
    """The plugin's limitations file is measured against one release line; it must say which.

    ``version`` in plugin.json pins users until it changes, so it is bumped whenever the plugin
    changes -- which is why only major.minor is held to the library's version, not the whole of
    it.
    """
    manifest = json.loads(
        (PLUGIN_ROOT / "liberaqt" / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["version"].split(".")[:2] == liberaqt.__version__.split(".")[:2]


# ------------------------------------------------------------------ skills and agents


@pytest.mark.parametrize("skill", sorted(PLUGIN_ROOT.glob("*/skills/*/SKILL.md"))
                         + sorted(DEV_ROOT.glob("skills/*/SKILL.md")),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_every_skill_is_named_after_its_directory(skill):
    meta = _frontmatter(skill)
    assert meta["name"] == skill.parent.name
    # Claude Code truncates the description at 1,536 characters; past that, triggering suffers.
    assert 50 < len(meta["description"]) <= 1536


@pytest.mark.parametrize("agent", sorted(PLUGIN_ROOT.glob("*/agents/*.md"))
                         + sorted(DEV_ROOT.glob("agents/*.md")),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_every_agent_is_well_formed(agent):
    meta = _frontmatter(agent)
    assert meta["name"] == agent.stem
    assert ":" not in meta["name"], "':' is reserved for plugin scoping; Claude Code skips the file"
    assert meta["description"] and meta["tools"]


@pytest.mark.parametrize("agent", sorted(PLUGIN_ROOT.glob("*/agents/*.md")),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_a_plugin_agent_preloads_only_skills_its_plugin_has(agent):
    """A plugin skill registers as ``<plugin>:<dir>``, and that is the name ``skills:`` takes.

    Measured: with the scoped names, the agent starts with both skills' full text in context.
    """
    plugin = agent.parents[1]
    have = {f"{plugin.name}:{s.parent.name}" for s in plugin.glob("skills/*/SKILL.md")}
    for wanted in _frontmatter(agent).get("skills", []):
        assert wanted in have, f"{agent.name} preloads {wanted!r}; the plugin has {sorted(have)}"


@pytest.mark.parametrize("page", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(page):
    text = page.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#:\s]+)\)", text):
        assert (page.parent / target).exists(), f"{page.name} links to missing {target}"


# ------------------------------------------------------------------ the API they teach


@pytest.mark.parametrize("page", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_python_block_parses_and_uses_real_attributes(page):
    problems = []
    for block in _python_blocks(page.read_text(encoding="utf-8")):
        try:
            tree = ast.parse(block)
        except SyntaxError as exc:
            problems.append(f"does not parse: {exc.msg} in\n{block}")
            continue
        checker = _Checker()
        checker.visit(tree)
        problems += checker.problems
    assert not problems, f"{page.relative_to(ROOT)}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("page", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_import_resolves(page):
    """Covers imports anywhere, including inside ``python -c "..."`` in a shell block."""
    problems = []
    for module, names in re.findall(r"from (liberaqt(?:\.\w+)*) import ([\w, ]+)",
                                    page.read_text(encoding="utf-8")):
        imported = importlib.import_module(module)
        for name in (n.strip() for n in names.split(",")):
            if name and not hasattr(imported, name):
                problems.append(f"{module} has no {name}")
    assert not problems, f"{page.relative_to(ROOT)}: {problems}"


@pytest.mark.parametrize("page", MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_inline_code_names_real_attributes(page):
    """Prose carries most of the API: tables and sentences quote it in backticks."""
    problems = []
    for span in re.findall(r"`([^`\n]+)`", page.read_text(encoding="utf-8")):
        for receiver, attr in re.findall(r"(?<![\w.'\"])([A-Za-z_]\w*)\.([A-Za-z_]\w*)", span):
            kind = CONVENTIONAL.get(receiver)
            if kind is None or attr.startswith("__") or (kind[0], attr) in ABSENT:
                continue
            if not _has(kind, attr):
                problems.append(f"`{span}`: {kind[0].__name__} has no {attr!r}")
        for attr in re.findall(r"expect\([^)]*\)\.(?:not_\.)?(\w+)", span):
            if not _has(ASSERTIONS, attr):
                problems.append(f"`{span}`: no assertion named {attr!r}")
    assert not problems, f"{page.relative_to(ROOT)}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize(("owner", "attr"), sorted(ABSENT, key=str),
                         ids=lambda v: getattr(v, "__name__", v))
def test_what_the_material_says_is_missing_is_still_missing(owner, attr):
    assert not hasattr(owner, attr), (
        f"{owner.__name__}.{attr} exists now, so this is false: {ABSENT[(owner, attr)]}")
