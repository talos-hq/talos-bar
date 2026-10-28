"""Clock: time over date, like the Plasma digital clock; world zones in its popup."""

from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from tzlocal import get_localzone

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
    return "UTC" if zone in {"Etc/UTC", "Etc/GMT"} else zone.split("/")[-1].replace("_", " ")


def _hm(t: datetime) -> str:
    return f"{t:%I:%M %p}".lstrip("0")


@item("clock", label="Clock", order=0, plain=True)
@every(seconds=1)
def refresh(ctx: Context) -> Output:
    now = datetime.now().astimezone()
    return {"text": _hm(now), "subtext": f"{now:%m/%d/%y}"}


class LocalClock(TypedDict):
    city: str
    time: str


class ZoneClock(LocalClock):
    day: str
    difference: str


class WorldClock(TypedDict):
    component: str
    date: str
    local: LocalClock
    rows: list[ZoneClock]


def _hms(t: datetime) -> str:
    return f"{t:%I:%M:%S %p}".lstrip("0")


def _difference(seconds: int) -> str:
    if seconds == 0:
        return "same time"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, seconds_part = divmod(remainder, 60)
    parts = [
        f"{value} {unit}{'s' if value != 1 else ''}"
        for value, unit in [(hours, "hour"), (minutes, "minute"), (seconds_part, "second")]
        if value
    ]
    return " ".join(parts) + (" later" if seconds > 0 else " earlier")


def world_at(now: datetime, local_zone: ZoneInfo) -> WorldClock:
    """One instant rendered in local time and the configured remote zones."""
    local = now.astimezone(local_zone)
    local_city = _city(local_zone.key)
    local_offset = local.utcoffset() or timedelta()
    ordered: list[tuple[int, ZoneClock]] = []
    for zone in ZONES:
        remote = now.astimezone(ZoneInfo(zone))
        city = _city(zone)
        difference = int(((remote.utcoffset() or timedelta()) - local_offset).total_seconds())
        if city == local_city and difference == 0:
            continue
        ordered.append(
            (
                difference,
                {
                    "city": city,
                    "time": _hms(remote),
                    "day": f"{remote:%A}" if remote.date() != local.date() else "",
                    "difference": _difference(difference),
                },
            )
        )
    ordered.sort(key=lambda entry: (entry[0], entry[1]["city"]))
    return {
        "component": "Clock.qml",
        "date": f"{local:%A, %B} {local.day}, {local:%Y}",
        "local": {"city": local_city, "time": _hms(local)},
        "rows": [row for _, row in ordered],
    }


@popup("clock", hover=True)
def world(ctx: Context) -> dict[str, Any]:
    return dict(world_at(datetime.now(timezone.utc), get_localzone()))
