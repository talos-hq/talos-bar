"""Today's meetings, from scripts/calendar_agenda.py (carried over from
~/dev/nixos calendar-py unchanged).

The script prints JSON: date, events, done, tomorrow, next, live, following,
next_label, countdown, next_hour_label, error. It reads the iCal file calendar-sync writes;
without that file this glue adds nothing. TALOS_BAR_AGENDA_CMD, when set,
runs an external command instead.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

from talos_bar.glue import Context, Output, action, every, item, popup, run, run_script

CMD = os.environ.get("TALOS_BAR_AGENDA_CMD", "")
# calendar_agenda.SOURCE, without importing the script just to decide.
ENABLED = bool(CMD) or (Path.home() / ".local/share/calendar-sync/google.ics").exists()


class Row(TypedDict):
    """One meeting, as calendar_agenda.Row serialises it."""

    title: str
    time: str
    start: float
    end: float
    all_day: bool
    status: str
    meeting: bool
    location: str
    url: str
    link_kind: str
    join_url: str
    details: str
    emails: list[str]
    phones: list[str]
    tomorrow: bool


class Agenda(TypedDict, total=False):
    """calendar_agenda.Feed; empty when the script printed nothing."""

    date: str
    events: list[Row]
    done: list[Row]
    tomorrow: list[Row]
    next: Row | None
    live: bool
    following: Row | None
    next_label: str
    countdown: str
    next_hour_label: str
    error: str


class AgendaPopup(Agenda):
    component: str


def _zoom_app_installed() -> bool:
    """Whether something handles zoommtg:// links (the Zoom desktop app)."""
    if not shutil.which("xdg-mime"):
        return True  # can't tell; keep the app links
    handler = run(["xdg-mime", "query", "default", "x-scheme-handler/zoommtg"], timeout=5)
    return bool(handler.strip())


def _fetch(ctx: Context) -> Agenda:
    raw = run([CMD], timeout=25) if CMD else run_script("calendar_agenda")
    agenda = cast("Agenda", json.loads(raw or "{}"))
    zoom_app = ctx.cache.get("zoom_app")
    if not isinstance(zoom_app, bool):
        zoom_app = ctx.cache["zoom_app"] = _zoom_app_installed()
    if not zoom_app:
        # Without the Zoom app a zoommtg:// link does nothing; use the web link.
        rows = [*agenda.get("events", []), *agenda.get("done", []), *agenda.get("tomorrow", [])]
        rows += [row for row in (agenda.get("next"), agenda.get("following")) if row]
        for row in rows:
            row["join_url"] = ""
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
        # A bare clock time reads as "starting now"; say how far off it is.
        next_hour = a.get("next_hour_label")
        if a.get("live"):
            text = f"On call  │  {next_hour}" if next_hour else "In progress"
        elif nxt:
            text = f"Next {_hm(nxt['start'])} · {a.get('countdown', '')}".rstrip(" ·")
        else:
            text = "—"
        if following and not next_hour:
            text += f"  │  Next {_hm(following['start'])}"
        out: Output = {"text": text, "icon": "view-calendar-agenda"}
        if a.get("live"):
            out["dot"] = "#e5322d"
        return out

    @popup("agenda", hover=True)
    def agenda(ctx: Context) -> AgendaPopup:
        cached = ctx.cache.get("agenda")
        a = cast("Agenda", cached) if isinstance(cached, dict) else _fetch(ctx)
        return {**a, "component": "Agenda.qml"}

    @action("agenda", "open_calendar")
    def open_calendar(ctx: Context, payload: str) -> None:
        # KOrganizer on KDE; elsewhere Google Calendar in the browser.
        if shutil.which("kioclient"):
            subprocess.Popen(["kioclient", "exec", "org.kde.korganizer.desktop"])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", "https://calendar.google.com"])
