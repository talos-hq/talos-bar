"""TASKS.md checklist, from scripts/tasks_cli.py (carried over from
~/dev/nixos system/tasks).

`list` gives {open, synced, top}; `read` the whole file; `save64 <b64>`
commits an edit locally (a timer pushes it). Enabled when the tasks clone
exists (TASKS_REPO, default ~/dev/tasks). TALOS_BAR_TASKS_CMD, when set,
runs an external command instead.

Agents change the file through `talos-tasks` (talos_bar/tasks_md.py) while
the editor may hold a draft. A save merges the draft with such a change
(tasks_md.merge) and only stops on overlapping edits. The tab's badge counts
open items an agent has claimed.
"""

import base64
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

from talos_bar.glue import Context, Output, action, every, item, popup, run, run_script
from talos_bar.tasks_md import claimed_open, merge

CMD = os.environ.get("TALOS_BAR_TASKS_CMD", "")
REPO = Path(os.environ.get("TASKS_REPO", Path.home() / "dev" / "tasks"))
ENABLED = bool(CMD) or (REPO / ".git").exists()


class _SaveLineage(TypedDict):
    ancestors: set[bytes]
    text: str  # what our last save wrote, merged
    draft: str  # the editor text that save was given, before merging


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


def _tooltip(data: TaskList, claimed: dict[str, list[str]] | None = None) -> str:
    claimed = claimed or {}
    marks: dict[bool | None, str] = {None: "•", True: "☑", False: "☐"}
    rows: list[str] = []
    for t in data.get("top", []):
        text = t.get("text", "")
        agents = claimed.get(text)
        mark = f"  ⚙ {', '.join(agents)}" if agents else ""
        rows.append(f"{marks.get(t.get('check'), '•')} {text}{mark}")
    tip = "\n".join(rows) or "Nothing yet"
    return tip if data.get("synced", True) else tip + "\nedits waiting for the next sync"


def _claimed() -> dict[str, list[str]]:
    """Open items an agent has claimed: text -> agents. Local clone only."""
    if CMD:
        return {}
    try:
        text = (REPO / "TASKS.md").read_text()
    except OSError:
        return {}
    return {it.text: it.claimed_by for it in claimed_open(text)}


if ENABLED:

    @item("tasks", label="Tasks", order=20)
    @every(seconds=30)
    def refresh(ctx: Context) -> Output:
        data = _list(ctx)
        claimed = _claimed()
        out: Output = {
            "text": str(data.get("open", 0)),
            "icon": "view-task",
            "tooltip": _tooltip(data, claimed),
        }
        if claimed:
            out["badge"] = f"⚙{len(claimed)}"  # open items an agent is working on
            out["badge_color"] = "#8e6cef"
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
        # Each queued edit may use a different acknowledged ancestor. Keep
        # their hashes and only one document body, rather than full snapshots.
        cached_chain = ctx.cache.get("tasks_save_chain")
        # Glue modules are reimported on hot reload; cache plain structural
        # data so the lineage does not depend on a previous module's class.
        chain: _SaveLineage | None = (
            cast("_SaveLineage", cached_chain) if isinstance(cached_chain, dict) else None
        )
        current = _tasks("read")
        base_hash = sha256(base.encode()).digest()
        # When the editor's base is one of our own saves, the file was last
        # written from our last draft (plus whatever that save merged in), so
        # the draft is the common ancestor; otherwise the editor's base is.
        # (.get: a chain cached by the module before a hot reload has no draft.)
        in_lineage = chain is not None and base_hash in chain["ancestors"]
        merge_base = chain.get("draft", chain["text"]) if in_lineage and chain is not None else base
        draft = text
        if current not in (merge_base, text):
            # Someone else (an agent's `talos-tasks`, another editor) changed
            # the file since: keep both sides unless they touched the same lines.
            merged = merge(text, merge_base, current)
            if merged is None:
                raise RuntimeError("TASKS.md changed outside this editor on the same lines; draft kept")
            text = merged
        if chain is None or not in_lineage:
            chain = {"ancestors": set(), "text": "", "draft": ""}
        chain["ancestors"].update(sha256(t.encode()).digest() for t in (base, draft, text))
        # A failed commit may still have written this text. Retain its lineage
        # so a later edit can retry without mistaking our own write for another editor.
        chain["text"] = text
        chain["draft"] = draft
        ctx.cache["tasks_save_chain"] = chain
        encoded = base64.b64encode(text.encode()).decode()
        if CMD:
            _tasks("save64", encoded)
        else:
            _tasks("save64", encoded, base64.b64encode(current.encode()).decode())
