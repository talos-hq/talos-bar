"""Today's meetings, from the calendar-agenda backend (~/dev/nixos calendar-py).

The backend prints JSON: date, events, done, tomorrow, next, live, following,
next_label, countdown, error. Its path comes from TALOS_BAR_AGENDA_CMD (the
NixOS service sets it), else `calendar-agenda` on PATH; with neither, this
glue adds nothing.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any

from talos_bar.glue import Context, Output, action, every, item, popup, run

CMD = os.environ.get("TALOS_BAR_AGENDA_CMD") or shutil.which("calendar-agenda") or ""


def _fetch(ctx: Context) -> dict[str, Any]:
    agenda: dict[str, Any] = json.loads(run([CMD], timeout=25) or "{}")
    ctx.cache["agenda"] = agenda
    return agenda


def _hm(epoch: float) -> str:
    return f"{datetime.fromtimestamp(epoch):%I:%M %p}".lstrip("0")


if CMD:

    @item("agenda", label="Calendar", order=10)
    @every(seconds=30)
    def refresh(ctx: Context) -> Output:
        a = _fetch(ctx)
        nxt, following = a.get("next"), a.get("following")
        text = "In progress" if a.get("live") else _hm(nxt["start"]) if nxt else "—"
        if following:
            text += f"  │  Next {_hm(following['start'])}"
        out: Output = {"text": text, "icon": "view-calendar-agenda"}
        if a.get("live"):
            out["dot"] = "#e5322d"
        return out

    @popup("agenda", hover=True)
    def agenda(ctx: Context) -> dict[str, Any]:
        a = ctx.cache.get("agenda") or _fetch(ctx)
        return {**a, "component": "Agenda.qml"}

    @action("agenda", "open_calendar")
    def open_calendar(ctx: Context, payload: str) -> None:
        subprocess.Popen(["kioclient", "exec", "org.kde.korganizer.desktop"])
