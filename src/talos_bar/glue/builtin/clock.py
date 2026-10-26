"""Clock: time over date, like the Plasma digital clock; world zones on hover."""

from datetime import datetime
from zoneinfo import ZoneInfo

from talos_bar.glue import Context, Output, every, item

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
    tip = "\n".join(f"{_city(z)}: {_hm(now.astimezone(ZoneInfo(z)))}" for z in ZONES)
    return {"text": _hm(now), "subtext": f"{now:%m/%d/%y}", "tooltip": tip}
