"""World clock: local time, with other zones in the tooltip."""

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


@item("clock", label="Clock")
@every(seconds=1)
def refresh(ctx: Context) -> Output:
    now = datetime.now().astimezone()
    tip = "\n".join(f"{_city(z)}: {now.astimezone(ZoneInfo(z)):%H:%M}" for z in ZONES)
    return {"text": f"{now:%a %d %b  %H:%M:%S}", "tooltip": tip}
