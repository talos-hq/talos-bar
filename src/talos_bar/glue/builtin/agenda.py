"""Today's meetings, from scripts/calendar_agenda.py (carried over from
~/dev/nixos calendar-py unchanged).

The script prints JSON: date, events, done, tomorrow, next, live, following,
next_label, countdown, error. It reads the iCal file calendar-sync writes;
without that file this glue adds nothing. TALOS_BAR_AGENDA_CMD, when set,
runs an external command instead.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from talos_bar.glue import Context, Output, action, every, item, popup, run, run_script

CMD = os.environ.get("TALOS_BAR_AGENDA_CMD", "")
# calendar_agenda.SOURCE, without importing the script just to decide.
ENABLED = bool(CMD) or (Path.home() / ".local/share/calendar-sync/google.ics").exists()


def _fetch(ctx: Context) -> dict[str, Any]:
    raw = run([CMD], timeout=25) if CMD else run_script("calendar_agenda")
    agenda: dict[str, Any] = json.loads(raw or "{}")
    ctx.cache["agenda"] = agenda
    return agenda


def _hm(epoch: float) -> str:
    return f"{datetime.fromtimestamp(epoch):%I:%M %p}".lstrip("0")


if ENABLED:

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
