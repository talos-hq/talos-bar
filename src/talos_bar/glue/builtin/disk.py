"""Disk: the only bar item that is usually invisible.

It appears when the root filesystem crosses `TALOS_BAR_DISK_THRESHOLD` (90% by
default) and disappears again once space is reclaimed, so the bar carries a
warning only when there is one. Clicking it opens the tree view
(`talos_bar.diskview`): where the space went, starting at `/`, mounted
filesystems excluded.

A refresh returning `None` is the glue API's "hide me" signal — the bar drops
the tab rather than drawing an empty slot.
"""

from __future__ import annotations

import os
import shutil
from typing import TypedDict

from talos_bar import diskview
from talos_bar.glue import Context, Output, every, item, on_click

DEFAULT_THRESHOLD = 0.90
# Free space is the number people act on; usage is what a "disk is X% full"
# reading means. They are complements, so one threshold serves both.
ROOT = "/"


class Alert(TypedDict):
    text: str
    subtext: str
    icon: str
    dot: str
    tooltip: str


def threshold() -> float:
    """Usage fraction at which the item appears.

    Read per refresh, not at import, so a user can raise or lower it in the
    environment without restarting the bar's interpreter.
    """
    raw = os.environ.get("TALOS_BAR_DISK_THRESHOLD")
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(max(value, 0.1), 1.0)


def usage(path: str = ROOT) -> tuple[int, int, float]:
    """(used bytes, total bytes, used fraction) for the filesystem holding `path`."""
    total, used, _free = shutil.disk_usage(path)
    return used, total, (used / total if total else 0.0)


@item("disk", label="Disk", order=30)
@every(seconds=60)
def refresh(ctx: Context) -> Output:
    used, total, fraction = usage()
    if fraction < threshold():
        return None
    percent = round(fraction * 100)
    free = total - used
    return {
        "text": f"{percent}%",
        "subtext": f"{diskview.human(free)} free",
        "icon": "drive-harddisk",
        "dot": "#e5322d" if fraction >= 0.95 else "#f0a92a",
        "tooltip": f"{diskview.human(used)} of {diskview.human(total)} used — click to see what is taking it",
    }


@on_click("disk")
def open_tree(ctx: Context) -> None:
    """Open the read-only tree view in the browser.

    The scan runs off the UI thread: click handlers are dispatched on the bar's
    thread pool, and the whole-root scan is measured in seconds.
    """
    diskview.open_view(ROOT)
