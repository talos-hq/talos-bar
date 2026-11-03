"""TASKS.md checklist, from scripts/tasks_cli.py (carried over from
~/dev/nixos system/tasks).

`list` gives {open, synced, top}; `read` the whole file; `save64 <b64>`
commits an edit locally (a timer pushes it). Enabled when the tasks clone
exists (TASKS_REPO, default ~/dev/tasks). TALOS_BAR_TASKS_CMD, when set,
runs an external command instead.
"""

import base64
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from talos_bar.glue import Context, Output, action, every, item, popup, run, run_script

CMD = os.environ.get("TALOS_BAR_TASKS_CMD", "")
REPO = Path(os.environ.get("TASKS_REPO", Path.home() / "dev" / "tasks"))
ENABLED = bool(CMD) or (REPO / ".git").exists()


class TaskLine(TypedDict, total=False):
    check: bool | None  # None: a plain bullet, not a checkbox
    text: str


class TaskList(TypedDict, total=False):
    """`tasks_cli list` output."""

    open: int
    synced: bool
    top: list[TaskLine]


class Editor(TypedDict):
    component: str
    text: str
    synced: bool


def _tasks(*args: str) -> str:
    if not CMD:
        return run_script("tasks_cli", *args)
    result = run([CMD, *args], timeout=25)
    # The external tasks command's read protocol uses print(file_text).
    return result.removesuffix("\n") if args == ("read",) else result


def _list(ctx: Context) -> TaskList:
    data = cast("TaskList", json.loads(_tasks("list") or "{}"))
    ctx.cache["tasks"] = data
    return data


def _tooltip(data: TaskList) -> str:
    marks: dict[bool | None, str] = {None: "•", True: "☑", False: "☐"}
    rows = [f"{marks.get(t.get('check'), '•')} {t.get('text', '')}" for t in data.get("top", [])]
    tip = "\n".join(rows) or "Nothing yet"
    return tip if data.get("synced", True) else tip + "\nedits waiting for the next sync"


if ENABLED:

    @item("tasks", label="Tasks", order=20)
    @every(seconds=30)
    def refresh(ctx: Context) -> Output:
        data = _list(ctx)
        out: Output = {"text": str(data.get("open", 0)), "icon": "view-task", "tooltip": _tooltip(data)}
        if not data.get("synced", True):
            out["dot"] = "#f67400"  # amber: committed locally, not pushed yet
        return out

    @popup("tasks")
    def editor(ctx: Context) -> Editor:
        cached = ctx.cache.get("tasks")
        data = cast("TaskList", cached) if isinstance(cached, dict) else _list(ctx)
        text = _tasks("read")
        return {"component": "Tasks.qml", "text": text, "synced": data.get("synced", True)}

    @action("tasks", "save")
    def save(ctx: Context, payload: str) -> None:
        edit = cast("dict[str, str]", json.loads(payload))
        text, base = edit["text"], edit["base"]
        # Autosaves queued before their predecessor was acknowledged share a
        # base. Compare against our last successful write, never stale disk.
        chain = ctx.cache.get("tasks_save_chain")
        current = _tasks("read")
        expected = base
        if isinstance(chain, tuple):
            old_base, saved = cast("tuple[str, str]", chain)
            if old_base == base and current != base:
                expected = saved
        encoded = base64.b64encode(text.encode()).decode()
        if current != expected and current != text:
            raise RuntimeError("TASKS.md changed outside this editor; draft kept")
        # A failed commit may still have written this text. Retain its base
        # so a later edit can retry without treating our own write as external.
        ctx.cache["tasks_save_chain"] = (base, text)
        if CMD:
            _tasks("save64", encoded)
        else:
            _tasks("save64", encoded, base64.b64encode(expected.encode()).decode())
