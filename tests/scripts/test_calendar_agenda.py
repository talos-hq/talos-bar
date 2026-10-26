"""Date boundaries and selection rules for the panel's private calendar feed."""

import os
import time
import unittest
from datetime import UTC, datetime
from typing import override

from calendar_agenda import (
    Feed,
    Link,
    LinkKind,
    Status,
    contacts,
    event_link,
    plain_text,
    today_agenda,
    zoom_join_url,
)
from calendar_ical import IcalEvent, parse_events

ZOOM = "\nLOCATION:https://example.zoom.us/j/123"


def ics(*events: str) -> list[IcalEvent]:
    """Parse VEVENT bodies written with plain newlines."""
    body = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        + "".join(
            "BEGIN:VEVENT\r\n" + event.replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n"
            for event in events
        )
        + "END:VCALENDAR\r\n"
    )
    return parse_events(body.encode())


def at(hour: int, minute: int = 0) -> datetime:
    """Return a UTC instant on 2026-10-22, when Los Angeles is UTC-7."""
    return datetime(2026, 10, 22, hour, minute, tzinfo=UTC)


def chosen(feed: Feed) -> str:
    """Return the selected event's title."""
    assert feed.next is not None  # noqa: S101 -- narrows for the type checker
    return feed.next.title


# The 2026-10-22 shape that left the tab on 10:00 AM: two linkless blocks
# overlapping Zoom meetings, one of them a two-hour block (times are UTC).
DAY = (
    "DTSTART:20261022T170000Z\nDTEND:20261022T180000Z\nSUMMARY:Standup" + ZOOM,
    "DTSTART:20261022T170000Z\nDTEND:20261022T190000Z\nSUMMARY:Focus block",
    "DTSTART:20261022T180000Z\nDTEND:20261022T190000Z\nSUMMARY:Hold",
    "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Noon call" + ZOOM,
    "DTSTART:20261022T200000Z\nDTEND:20261022T210000Z\nSUMMARY:One o'clock" + ZOOM,
)


class AgendaTests(unittest.TestCase):
    old_tz: str | None = None

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()

    @classmethod
    @override
    def tearDownClass(cls) -> None:
        if cls.old_tz is None:
            _ = os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = cls.old_tz
        time.tzset()

    def test_block_does_not_hold_the_tab_after_its_meeting_ends(self) -> None:
        feed = today_agenda(ics(*DAY), at(18, 38))  # 11:38 AM
        self.assertEqual(chosen(feed), "Noon call")
        self.assertEqual(feed.next_label, "12:00 PM")
        self.assertFalse(feed.live)
        # Every event, blocks included, stays in the hover agenda.
        self.assertEqual(len(feed.events) + len(feed.done), 5)

    def test_meeting_is_sticky_until_its_end(self) -> None:
        for moment in (at(17), at(17, 30), at(17, 59)):
            feed = today_agenda(ics(*DAY), moment)
            self.assertEqual(chosen(feed), "Standup")
            self.assertTrue(feed.live)
            self.assertEqual(feed.next_label, "Now")

    def test_end_boundary_advances_to_next_meeting(self) -> None:
        feed = today_agenda(ics(*DAY), at(18))  # Standup's end instant
        self.assertEqual(chosen(feed), "Noon call")
        self.assertFalse(feed.live)
        standup = next(row for row in feed.done if row.title == "Standup")
        self.assertIs(standup.status, Status.PAST)

    def test_start_boundary_makes_meeting_live(self) -> None:
        feed = today_agenda(ics(*DAY), at(18, 59))
        self.assertEqual(chosen(feed), "Noon call")
        self.assertFalse(feed.live)
        self.assertEqual(feed.countdown, "in 1 min")
        feed = today_agenda(ics(*DAY), at(19))
        self.assertEqual(chosen(feed), "Noon call")
        self.assertTrue(feed.live)

    def test_live_meeting_shows_the_back_to_back_one(self) -> None:
        feed = today_agenda(ics(*DAY), at(19, 30))  # 12:30, in the noon call
        self.assertTrue(feed.live)
        self.assertEqual(chosen(feed), "Noon call")
        assert feed.following is not None  # noqa: S101 -- narrows for the type checker
        self.assertEqual(feed.following.title, "One o'clock")

    def test_following_needs_a_live_meeting_and_a_near_start(self) -> None:
        self.assertIsNone(today_agenda(ics(*DAY), at(18, 30)).following)  # nothing live
        far = ics(
            "DTSTART:20261022T170000Z\nDTEND:20261022T180000Z\nSUMMARY:Call" + ZOOM,
            "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Later" + ZOOM,
            "DTSTART:20261022T173000Z\nDTEND:20261022T183000Z\nSUMMARY:Block",
        )
        # Later starts exactly an hour after Call ends; a block never counts.
        self.assertIsNone(today_agenda(far, at(17, 45)).following)

    def test_overlapping_meetings_keep_the_first_until_it_ends(self) -> None:
        events = ics(
            "DTSTART:20261022T170000Z\nDTEND:20261022T183000Z\nSUMMARY:Long" + ZOOM,
            "DTSTART:20261022T180000Z\nDTEND:20261022T190000Z\nSUMMARY:Overlap" + ZOOM,
        )
        self.assertEqual(chosen(today_agenda(events, at(18, 15))), "Long")
        self.assertEqual(chosen(today_agenda(events, at(18, 30))), "Overlap")

    def test_ahead_first_then_done_in_start_order(self) -> None:
        feed = today_agenda(ics(*DAY), at(19, 30))  # 12:30 PM
        self.assertEqual([row.title for row in feed.events], ["Noon call", "One o'clock"])
        self.assertEqual([row.title for row in feed.done], ["Focus block", "Standup", "Hold"])
        self.assertTrue(all(row.status is Status.PAST for row in feed.done))

    def test_finished_day_keeps_every_meeting_as_done(self) -> None:
        feed = today_agenda(ics(*DAY), at(23, 30))  # 4:30 PM
        self.assertEqual(feed.events, [])
        self.assertEqual(len(feed.done), 5)
        self.assertEqual(feed.countdown, "All of today's meetings are done")

    def test_tomorrow_appears_once_nothing_is_ahead(self) -> None:
        friday = (
            "DTSTART:20261023T163000Z\nDTEND:20261023T170000Z\nSUMMARY:Friday call" + ZOOM,
            "DTSTART;VALUE=DATE:20261023\nDTEND;VALUE=DATE:20261024\nSUMMARY:Holiday",
        )
        self.assertEqual(today_agenda(ics(*DAY, *friday), at(19, 30)).tomorrow, [])
        feed = today_agenda(ics(*DAY, *friday), at(23, 30))
        self.assertEqual([row.title for row in feed.tomorrow], ["Holiday", "Friday call"])
        self.assertTrue(all(row.tomorrow for row in feed.tomorrow))
        self.assertFalse(any(row.tomorrow for row in feed.done))
        self.assertEqual(len(feed.done), 5)

    def test_contacts_skip_links_and_zoom_dial_ins(self) -> None:
        [event] = ics(
            "DTSTART;VALUE=DATE:20261022\nSUMMARY:Intro\nDESCRIPTION:"
            "Reach me at jane.doe@example.com or (415) 555-0134."
            " https://example.zoom.us/j/123?uname=bot@example.org"
            " Dial +16699006833,,123456789# or +1 669 900 6833"
            " Meeting ID: 123 456 7890"
        )
        emails, phones = contacts(event)
        self.assertEqual(emails, ["jane.doe@example.com"])
        self.assertEqual(phones, ["(415) 555-0134", "+1 669 900 6833"])

    def test_email_stops_before_a_run_on_label(self) -> None:
        [event] = ics(
            "DTSTART;VALUE=DATE:20261022\nSUMMARY:Intro\n"
            "DESCRIPTION:Email: sam@site.com.auWebsite: none"
        )
        self.assertEqual(contacts(event)[0], ["sam@site.com.au"])

    def test_details_are_plain_text(self) -> None:
        self.assertEqual(
            plain_text("Agenda:<br>1. <b>Plan</b> &amp; review<br><br><br>Bring notes"),
            "Agenda:\n1. Plan & review\n\nBring notes",
        )

    def test_empty_day_is_not_reported_as_done(self) -> None:
        feed = today_agenda([], at(18))
        self.assertEqual(feed.done, [])
        self.assertEqual(feed.countdown, "No meetings today")

    def test_expired_events_are_never_selected(self) -> None:
        feed = today_agenda(ics(*DAY), at(21))
        self.assertIsNone(feed.next)
        self.assertFalse(feed.live)
        self.assertEqual(feed.countdown, "All of today's meetings are done")

    def test_transparent_or_typed_blocks_are_not_meetings(self) -> None:
        events = ics(
            "DTSTART:20261022T170000Z\nDTEND:20261022T200000Z\nSUMMARY:Free"
            + ZOOM
            + "\nTRANSP:TRANSPARENT",
            "DTSTART:20261022T170000Z\nDTEND:20261022T200000Z\nSUMMARY:Focus"
            + ZOOM
            + "\nX-GOOGLE-EVENT-TYPE:focusTime",
            "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Real" + ZOOM,
        )
        self.assertEqual(chosen(today_agenda(events, at(18))), "Real")

    def test_blocks_are_used_when_no_meeting_remains(self) -> None:
        events = ics(
            "DTSTART:20261022T170000Z\nDTEND:20261022T180000Z\nSUMMARY:Call" + ZOOM,
            "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Lunch",
        )
        self.assertEqual(chosen(today_agenda(events, at(17, 30))), "Call")
        self.assertEqual(chosen(today_agenda(events, at(18, 30))), "Lunch")
        feed = today_agenda(events, at(19, 30))
        self.assertEqual(chosen(feed), "Lunch")
        self.assertTrue(feed.live)

    def test_joinable_meeting_wins_same_start_time(self) -> None:
        feed = today_agenda(
            ics(
                "DTSTART:20261022T170000Z\nDTEND:20261022T190000Z\nSUMMARY:Calendar block",
                "DTSTART:20261022T170000Z\nDTEND:20261022T180000Z\nSUMMARY:Call" + ZOOM,
            ),
            at(16),
        )
        self.assertEqual(chosen(feed), "Call")
        self.assertEqual(len(feed.events), 2)

    def test_past_current_upcoming_and_cancelled(self) -> None:
        feed = today_agenda(
            ics(
                "DTSTART:20261022T160000Z\nDTEND:20261022T170000Z\nSUMMARY:Finished",
                "DTSTART:20261022T170000Z\nDTEND:20261022T180000Z\nSUMMARY:Current",
                "DTSTART:20261022T180000Z\nDTEND:20261022T190000Z\nSUMMARY:Next",
                "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Gone\nSTATUS:CANCELLED",
            ),
            at(17, 30),
        )
        self.assertEqual(
            [row.status for row in feed.done + feed.events],
            [Status.PAST, Status.CURRENT, Status.UPCOMING],
        )
        self.assertEqual(chosen(feed), "Current")

    def test_local_day_and_exclusive_all_day_end(self) -> None:
        feed = today_agenda(
            ics(
                "DTSTART:20261023T013000Z\nDTEND:20261023T020000Z\nSUMMARY:Tonight",
                "DTSTART:20261023T080000Z\nDTEND:20261023T090000Z\nSUMMARY:Tomorrow",
                "DTSTART;VALUE=DATE:20261021\nDTEND;VALUE=DATE:20261022\nSUMMARY:Ended yesterday",
                "DTSTART;VALUE=DATE:20261022\nDTEND;VALUE=DATE:20261023\nSUMMARY:All day",
            ),
            at(17),
        )
        self.assertEqual([row.title for row in feed.events], ["All day", "Tonight"])
        self.assertEqual(chosen(feed), "Tonight")
        self.assertEqual(feed.next_label, "6:30 PM")

    def test_overnight_and_dst_boundary(self) -> None:
        feed = today_agenda(
            ics(
                "DTSTART:20261101T063000Z\nDTEND:20261101T083000Z\nSUMMARY:Overnight",
                "DTSTART:20261102T073000Z\nDTEND:20261102T080000Z\nSUMMARY:Late tonight",
            ),
            datetime(2026, 11, 1, 8, tzinfo=UTC),
        )
        self.assertEqual(len(feed.events), 2)
        self.assertEqual(chosen(feed), "Overnight")
        self.assertTrue(feed.events[0].time.startswith("Yesterday"))

    def test_zoom_links_open_the_app_directly(self) -> None:
        self.assertEqual(
            zoom_join_url("https://us02web.zoom.us/j/12345678901?pwd=abc.1"),
            "zoommtg://zoom.us/join?action=join&confno=12345678901&pwd=abc.1",
        )
        self.assertEqual(
            zoom_join_url("https://example.zoom.us/j/123456789"),
            "zoommtg://zoom.us/join?action=join&confno=123456789",
        )
        self.assertEqual(zoom_join_url("https://example.zoom.us/my/someone"), "")
        self.assertEqual(zoom_join_url("https://zoom.us.evil.test/j/123456789"), "")
        [row] = today_agenda(
            ics(
                "DTSTART:20261022T190000Z\nDTEND:20261022T200000Z\nSUMMARY:Call"
                "\nLOCATION:https://example.zoom.us/j/123456789"
            ),
            at(18),
        ).events
        self.assertEqual(row.join_url, "zoommtg://zoom.us/join?action=join&confno=123456789")

    def test_zoom_domain_validation_and_safe_fallback(self) -> None:
        [event] = ics(
            "DTSTART;VALUE=DATE:20261022\nSUMMARY:Meeting\n"
            "DESCRIPTION:https://zoom.us.evil.test/j/123 https://example.zoom.us/j/456"
        )
        self.assertEqual(event_link(event), Link("https://example.zoom.us/j/456", LinkKind.ZOOM))
        [event] = ics(
            "DTSTART;VALUE=DATE:20261022\nSUMMARY:Meeting\n"
            "DESCRIPTION:https://zoom.us.evil.test/j/123\nURL:javascript:alert(1)"
        )
        self.assertEqual(event_link(event), Link("", LinkKind.NONE))


if __name__ == "__main__":
    _ = unittest.main()
