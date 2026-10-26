"""Glue API: drop a Python file in a glue directory to add items to the bar.

from talos_bar.glue import Context, Output, every, item, on_click

@item("hello", label="Hello")
@every(seconds=5)
def refresh(ctx: Context) -> Output:
    return "hi"
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import io
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict


class Rich(TypedDict, total=False):
    text: str
    subtext: str  # a smaller second line under text (the clock's date)
    icon: str  # freedesktop icon name, e.g. "view-calendar-agenda"
    dot: str  # a small status dot after the icon, as a colour ("#e5322d")
    tooltip: str


Output = str | Rich | None
RefreshFn = Callable[["Context"], Output]
ClickFn = Callable[["Context"], None]
# What a popup shows: a dict handed to QML as `content`. With no "component"
# key it is drawn by the built-in ListPopup.qml ({title, subtitle, rows:
# [{text, subtext, icon, url}]}); otherwise "component" names a .qml file
# beside the glue module (or a built-in one) that draws it.
PopupFn = Callable[["Context"], dict[str, Any]]
# Called from a popup's QML through bar.action(id, name, payload).
ActionFn = Callable[["Context", str], None]


@dataclass
class Item:
    id: str
    label: str
    refresh: RefreshFn | None = None
    interval: float = 60.0
    click: ClickFn | None = None
    platforms: frozenset[str] | None = None
    source: Path | None = None
    order: int = 100  # left to right, lowest first
    plain: bool = False  # bare text like the clock, not a highlighted tab
    popup: PopupFn | None = None
    hover: bool = False  # popup opens on hover (and closes on leave), not on click
    actions: dict[str, ActionFn] = field(default_factory=dict[str, ActionFn])


@dataclass
class Context:
    os: str = sys.platform
    cache: dict[str, Any] = field(default_factory=dict[str, Any])


# Filled while a glue module is imported; read back by load_file().
_registry: dict[str, Item] = {}
_intervals: dict[int, float] = {}


def item(
    id: str,
    label: str = "",
    platforms: set[str] | None = None,
    order: int = 100,
    plain: bool = False,
) -> Callable[[RefreshFn], RefreshFn]:
    def deco(fn: RefreshFn) -> RefreshFn:
        it = _registry.setdefault(id, Item(id=id, label=label or id))
        it.refresh = fn
        it.order = order
        it.plain = plain
        it.interval = _intervals.get(id_of(fn), it.interval)
        it.platforms = frozenset(platforms) if platforms else None
        return fn

    return deco


def every(seconds: float) -> Callable[[RefreshFn], RefreshFn]:
    """Refresh interval; place it below @item."""

    def deco(fn: RefreshFn) -> RefreshFn:
        _intervals[id_of(fn)] = seconds
        return fn

    return deco


def popup(id: str, hover: bool = False) -> Callable[[PopupFn], PopupFn]:
    """The tab opens a popup below it; this builds what the popup shows."""

    def deco(fn: PopupFn) -> PopupFn:
        it = _registry.setdefault(id, Item(id=id, label=id))
        it.popup, it.hover = fn, hover
        return fn

    return deco


def action(id: str, name: str) -> Callable[[ActionFn], ActionFn]:
    """Handle ``bar.action(id, name, payload)`` sent by a popup's QML."""

    def deco(fn: ActionFn) -> ActionFn:
        _registry.setdefault(id, Item(id=id, label=id)).actions[name] = fn
        return fn

    return deco


def on_click(id: str) -> Callable[[ClickFn], ClickFn]:
    def deco(fn: ClickFn) -> ClickFn:
        _registry.setdefault(id, Item(id=id, label=id)).click = fn
        return fn

    return deco


def run(cmd: list[str], timeout: float = 10.0) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout


SCRIPTS = Path(__file__).parent.parent / "scripts"
_script_lock = threading.Lock()


def run_script(name: str, *args: str) -> str:
    """Run a script from talos_bar/scripts in-process and return its stdout.

    The scripts are carried over unchanged from ~/dev/nixos (calendar-py,
    tasks): they import their siblings as top-level modules and print to
    stdout, so this puts their directory on sys.path and captures print().
    ``main()`` is called with ``[name, *args]`` when it takes argv. The lock
    keeps one script at a time on the process-wide stdout redirect.
    """
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    module = importlib.import_module(name)
    main: Callable[..., object] = module.main
    takes_argv = bool(inspect.signature(main).parameters)
    out = io.StringIO()
    with _script_lock, contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main([name, *args]) if takes_argv else main()
    return out.getvalue()


def id_of(fn: object) -> int:
    return id(fn)


def load_file(path: Path) -> list[Item]:
    """Import one glue file and return the items it registered (empty on error)."""
    _registry.clear()
    _intervals.clear()
    spec = importlib.util.spec_from_file_location(f"talos_glue_{path.stem}", path)
    if spec is None or spec.loader is None:
        return []
    try:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    except Exception:
        traceback.print_exc()
        return []
    items = [i for i in _registry.values() if i.platforms is None or os_key() in i.platforms]
    for i in items:
        i.source = path
    return items


def glue_dirs() -> list[Path]:
    return [Path(__file__).parent / "builtin", Path.home() / ".config" / "talos-bar" / "glue"]


def os_key() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
