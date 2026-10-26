"""TASKS.md checklist, from scripts/tasks_cli.py (carried over from
~/dev/nixos system/tasks unchanged).

`list` gives {open, synced, top}; `read` the whole file; `save64 <b64>`
commits an edit locally (a timer pushes it). Enabled when the tasks clone
exists (TASKS_REPO, default ~/dev/tasks). TALOS_BAR_TASKS_CMD, when set,
runs an external command instead.
"""

import base64
import json
import os
from pathlib import Path
from typing import Any

from talos_bar.glue import Context, Output, action, every, item, popup, run, run_script

CMD = os.environ.get("TALOS_BAR_TASKS_CMD", "")
REPO = Path(os.environ.get("TASKS_REPO", Path.home() / "dev" / "tasks"))
ENABLED = bool(CMD) or (REPO / ".git").exists()


def _tasks(*args: str) -> str:
    return run([CMD, *args], timeout=25) if CMD else run_script("tasks_cli", *args)


def _list(ctx: Context) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_tasks("list") or "{}")
    ctx.cache["tasks"] = data
    return data


def _tooltip(data: dict[str, Any]) -> str:
    marks = {None: "•", True: "☑", False: "☐"}
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
    def editor(ctx: Context) -> dict[str, Any]:
        data = ctx.cache.get("tasks") or _list(ctx)
        text = _tasks("read").removesuffix("\n")
        return {"component": "Tasks.qml", "text": text, "synced": data.get("synced", True)}

    @action("tasks", "save")
    def save(ctx: Context, payload: str) -> None:
        _tasks("save64", base64.b64encode(payload.encode()).decode())
