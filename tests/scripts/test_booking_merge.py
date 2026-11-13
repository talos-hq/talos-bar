"""Matching calendar events to bookings and labelling their payment."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from booking_merge import Booking, booking_title, load_bookings, match, parse_bookings, payment_state
from calendar_agenda import agenda_with_bookings
from calendar_ical import IcalEvent, parse_events

T = datetime(2026, 10, 22, 19, 0, tzinfo=UTC).timestamp()  # noon in Los Angeles


def booking(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "b1",
        "start_utc": "2026-10-22T19:00:00Z",
        "end_utc": "2026-10-22T20:00:00Z",
        "name": "Ada Lovelace",
        "email": "Ada@Example.com",
        # The server's shape (/api/bar/bookings): an object, not a string.
        "event_type": {"slug": "consultation", "title": "Consultation"},
        "status": "confirmed",
        "payment_status": "paid",
        "paid": True,
        "payment_label": "$Paid",
    }
    return {**base, **kw}


def ics(*events: str) -> list[IcalEvent]:
    body = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        + "".join("BEGIN:VEVENT\r\n" + e.replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n" for e in events)
        + "END:VCALENDAR\r\n"
    )
    return parse_events(body.encode())


def test_label_mapping() -> None:
    assert payment_state(booking()) == ("$Paid", "paid")
    assert payment_state(booking(paid=False, payment_label="$Paid")) == ("$Paid", "paid")
    unpaid = booking(paid=False, payment_status="unpaid", payment_label="unpaid")
    assert payment_state(unpaid) == ("unpaid", "unpaid")
    assert payment_state(booking(paid=False, payment_status="unpaid", payment_label="")) == ("unpaid", "unpaid")
    assert payment_state(booking(paid=False, payment_label="waived")) == ("waived", "other")
    assert payment_state(booking(paid=False, payment_label="refunded")) == ("refunded", "other")
    assert payment_state(booking(paid=False, payment_label="", payment_status="")) == ("", "other")


def test_parse_skips_cancelled_and_malformed() -> None:
    got = parse_bookings(
        {"bookings": [booking(), booking(id="c", status="cancelled"), booking(start_utc="nope"), "x"]}
    )
    assert [b.id for b in got] == ["b1"]
    assert got[0].email == "ada@example.com"
    assert got[0].start == T
    assert parse_bookings({"nope": 1}) == []


def test_event_type_object_and_string_titles() -> None:
    (one,) = parse_bookings({"bookings": [booking()]})
    assert one.event_type == "Consultation"
    assert booking_title(one) == "Consultation: Ada Lovelace"
    (slug_only,) = parse_bookings({"bookings": [booking(event_type={"slug": "x-y"})]})
    assert slug_only.event_type == "x-y"
    (plain,) = parse_bookings({"bookings": [booking(event_type="Chat")]})
    assert booking_title(plain) == "Chat: Ada Lovelace"


def test_match_by_email_within_five_minutes() -> None:
    [b] = parse_bookings({"bookings": [booking()]})
    assert match([b], T + 299, ["ada@example.com"], "Anything") == b
    assert match([b], T + 301, ["ada@example.com"], "Anything") is None
    assert match([b], T, ["someone@else.com"], "Anything") is None


def test_match_falls_back_to_name_in_title() -> None:
    [b] = parse_bookings({"bookings": [booking()]})
    assert match([b], T - 120, [], "Consult with ada lovelace") == b
    assert match([b], T - 120, [], "Consult with Grace") is None


def test_email_match_beats_name_and_closest_wins() -> None:
    near: list[Booking] = parse_bookings(
        {
            "bookings": [
                booking(id="name", email="x@y.com"),
                booking(id="email-far", start_utc="2026-10-22T19:04:00Z"),
                booking(id="email-near", start_utc="2026-10-22T19:01:00Z"),
            ]
        }
    )
    assert match(near, T, ["ada@example.com"], "Ada Lovelace").id == "email-near"  # type: ignore[union-attr]


def test_agenda_rows_get_payment_and_missing_bookings_appear() -> None:
    events = ics(
        "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Consultation\n"
        "DESCRIPTION:Booked by ada@example.com\nLOCATION:https://example.zoom.us/j/123456789",
        "DTSTART:20261022T210000Z\nDTEND:20261022T213000Z\nSUMMARY:Gym",
    )
    bookings = parse_bookings(
        {
            "bookings": [
                booking(),
                booking(
                    id="b2",
                    start_utc="2026-10-22T22:00:00Z",
                    end_utc="2026-10-22T22:30:00Z",
                    name="Grace Hopper",
                    email="grace@example.com",
                    paid=False,
                    payment_status="unpaid",
                    payment_label="unpaid",
                ),
            ]
        }
    )
    feed = agenda_with_bookings(events, bookings, datetime(2026, 10, 22, 18, 0, tzinfo=UTC))
    by_title = {row.title: row for row in feed.events}
    assert set(by_title) == {"Consultation", "Gym", "Consultation: Grace Hopper"}
    assert (by_title["Consultation"].payment_label, by_title["Consultation"].payment_state) == ("$Paid", "paid")
    assert by_title["Gym"].payment_state == ""
    added = by_title["Consultation: Grace Hopper"]
    assert (added.payment_label, added.payment_state, added.meeting) == ("unpaid", "unpaid", True)
    assert added.emails == ["grace@example.com"]
    assert feed.next is not None
    assert (feed.next.title, feed.next.payment_state) == ("Consultation", "paid")


def test_no_bookings_changes_nothing() -> None:
    events = ics("DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Solo")
    feed = agenda_with_bookings(events, [], datetime(2026, 10, 22, 18, 0, tzinfo=UTC))
    assert [(r.title, r.payment_label, r.payment_state) for r in feed.events] == [("Solo", "", "")]


def test_load_bookings_ignores_missing_bad_and_stale(tmp_path: Path) -> None:
    path = tmp_path / "bookings.json"
    assert load_bookings(path) == []
    path.write_text("{not json")
    assert load_bookings(path) == []
    path.write_text(json.dumps({"bookings": [booking()]}))
    assert [b.id for b in load_bookings(path)] == ["b1"]
    old = path.stat().st_mtime - 2 * 86400
    os.utime(path, (old, old))
    assert load_bookings(path) == []
