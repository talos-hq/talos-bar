"""TASKS.md checklist, from the tasks backend (~/dev/nixos system/tasks).

`tasks list` gives {open, synced, top}; `tasks read` the whole file;
`tasks save64 <b64>` commits an edit locally (a timer pushes it). The path
comes from TALOS_BAR_TASKS_CMD, else `tasks` on PATH; with neither, this
glue adds nothing.
"""

import base64
import json
import os
import shutil
from typing import Any

from talos_bar.glue import Context, Output, action, every, item, popup, run

CMD = os.environ.get("TALOS_BAR_TASKS_CMD") or shutil.which("tasks") or ""


def _list(ctx: Context) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(run([CMD, "list"], timeout=25) or "{}")
    ctx.cache["tasks"] = data
    return data


def _tooltip(data: dict[str, Any]) -> str:
    marks = {None: "•", True: "☑", False: "☐"}
    rows = [f"{marks.get(t.get('check'), '•')} {t.get('text', '')}" for t in data.get("top", [])]
    tip = "\n".join(rows) or "Nothing yet"
    return tip if data.get("synced", True) else tip + "\nedits waiting for the next sync"


if CMD:

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
        text = run([CMD, "read"], timeout=25).removesuffix("\n")
        return {"component": "Tasks.qml", "text": text, "synced": data.get("synced", True)}

    @action("tasks", "save")
    def save(ctx: Context, payload: str) -> None:
        run([CMD, "save64", base64.b64encode(payload.encode()).decode()], timeout=25)
