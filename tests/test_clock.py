from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from talos_bar.glue.builtin import clock

REFERENCE = datetime(2026, 10, 28, 23, 32, 52, tzinfo=timezone.utc)


def test_world_clock_matches_detailed_reference() -> None:
    data = clock.world_at(REFERENCE, ZoneInfo("America/Los_Angeles"))
    assert data["date"] == "Wednesday, October 28, 2026"
    assert data["local"] == {"city": "Los Angeles", "time": "4:32:52 PM"}
    rows = data["rows"]
    assert [row["city"] for row in rows] == [
        "Denver",
        "Chicago",
        "New York",
        "UTC",
        "Luxembourg",
        "Moscow",
        "Tokyo",
        "Sydney",
    ]
    assert rows[0] == {"city": "Denver", "time": "5:32:52 PM", "day": "", "difference": "1 hour later"}
    assert rows[4] == {
        "city": "Luxembourg",
        "time": "12:32:52 AM",
        "day": "Thursday",
        "difference": "8 hours later",
    }
    assert rows[-1] == {
        "city": "Sydney",
        "time": "10:32:52 AM",
        "day": "Thursday",
        "difference": "18 hours later",
    }
    assert "title" not in data
    assert "KOrganizer" not in str(data)


@pytest.mark.parametrize(
    "hour,denver,new_york", [(8, "same time", "2 hours later"), (9, "1 hour later", "3 hours later")]
)
def test_offsets_follow_actual_dst_transition(hour: int, denver: str, new_york: str) -> None:
    instant = datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc)
    data = clock.world_at(instant, ZoneInfo("America/Los_Angeles"))
    rows = {row["city"]: row for row in data["rows"]}
    assert rows["Denver"]["difference"] == denver
    assert rows["New York"]["difference"] == new_york


def test_previous_day_and_fractional_local_offset() -> None:
    data = clock.world_at(REFERENCE, ZoneInfo("Asia/Kathmandu"))
    utc = next(row for row in data["rows"] if row["city"] == "UTC")
    assert data["local"] == {"city": "Kathmandu", "time": "5:17:52 AM"}
    assert utc["day"] == "Wednesday"
    assert utc["difference"] == "5 hours 45 minutes earlier"


@pytest.mark.parametrize("local_zone,city", [("America/Denver", "Denver"), ("Etc/UTC", "UTC")])
def test_local_city_is_not_duplicated(local_zone: str, city: str) -> None:
    data = clock.world_at(REFERENCE, ZoneInfo(local_zone))
    assert data["local"]["city"] == city
    assert city not in [row["city"] for row in data["rows"]]
