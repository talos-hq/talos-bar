"""Clock: time over date, like the Plasma digital clock; world zones in its popup."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from talos_bar.glue import Context, Output, every, item, popup

ZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "Australia/Sydney",
    "Europe/Luxembourg",
    "Europe/Moscow",
    "Asia/Tokyo",
    "UTC",
]


def _city(zone: str) -> str:
    return zone.split("/")[-1].replace("_", " ")


def _hm(t: datetime) -> str:
    return f"{t:%I:%M %p}".lstrip("0")


@item("clock", label="Clock", order=0, plain=True)
@every(seconds=1)
def refresh(ctx: Context) -> Output:
    now = datetime.now().astimezone()
    return {"text": _hm(now), "subtext": f"{now:%m/%d/%y}"}


@popup("clock", hover=True)
def world(ctx: Context) -> dict[str, Any]:
    now = datetime.now().astimezone()
    rows = [{"text": _city(z), "subtext": _hm(now.astimezone(ZoneInfo(z)))} for z in ZONES]
    return {"title": "World clock", "subtitle": f"{now:%A, %B %d}", "rows": rows}
