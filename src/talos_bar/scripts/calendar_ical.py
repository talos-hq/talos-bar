"""The one place that touches icalendar's untyped API.

Every VEVENT is parsed eagerly into an `IcalEvent`, and written from an
`IcalOutput`; nothing outside this module sees an icalendar object.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from icalendar import Alarm, Calendar, Component, Event


class ParseError(ValueError):
    """The calendar file holds a value of an unexpected type."""


@dataclass(frozen=True, slots=True)
class IcalEvent:
    """One VEVENT, with every field the agenda uses already typed."""

    summary: str
    start: date | datetime
    end: date | datetime | None
    duration: timedelta | None
    cancelled: bool
    location: str
    description: str
    url: str
    # TRANSP:TRANSPARENT marks time that does not block availability.
    transparent: bool
    # Google's eventType ("default", "focusTime", "workingLocation", ...).
    event_type: str


def _text(component: Component, key: str) -> str:
    value = component.get(key)
    return "" if value is None else str(value)


def _when(component: Component, key: str) -> date | datetime | None:
    if key not in component:
        return None
    value = component.decoded(key)
    # datetime subclasses date, so this accepts both.
    if not isinstance(value, date):
        msg = f"{key} is not a date"
        raise ParseError(msg)
    return value


def _duration(component: Component) -> timedelta | None:
    if "DURATION" not in component:
        return None
    value = component.decoded("DURATION")
    if not isinstance(value, timedelta):
        msg = "DURATION is not a duration"
        raise ParseError(msg)
    return value


def parse_event(component: Component) -> IcalEvent | None:
    """Parse one VEVENT; return None for an event with no start."""
    start = _when(component, "DTSTART")
    if start is None:
        return None
    return IcalEvent(
        summary=_text(component, "SUMMARY"),
        start=start,
        end=_when(component, "DTEND"),
        duration=_duration(component),
        cancelled=_text(component, "STATUS").upper() == "CANCELLED",
        location=_text(component, "LOCATION"),
        description=_text(component, "DESCRIPTION"),
        url=_text(component, "URL"),
        transparent=_text(component, "TRANSP").upper() == "TRANSPARENT",
        event_type=_text(component, "X-GOOGLE-EVENT-TYPE"),
    )


def parse_events(data: bytes) -> list[IcalEvent]:
    """Parse every VEVENT in an iCalendar document."""
    events = (parse_event(c) for c in Calendar.from_ical(data).walk("VEVENT"))
    return [event for event in events if event is not None]


@dataclass(frozen=True, slots=True)
class IcalAlarm:
    """A display reminder, `trigger` relative to the event start."""

    description: str
    trigger: timedelta


@dataclass(frozen=True, slots=True)
class IcalOutput:
    """A VEVENT to write for KOrganizer."""

    uid: str
    start: date | datetime
    end: date | datetime
    stamp: datetime
    sequence: int
    summary: str
    description: str
    location: str
    url: str
    transparent: bool
    event_type: str
    alarms: tuple[IcalAlarm, ...]


def _event_component(event: IcalOutput) -> Event:
    component = Event()
    component.add("uid", event.uid)
    component.add("dtstart", event.start)
    component.add("dtend", event.end)
    component.add("dtstamp", event.stamp)
    component.add("last-modified", event.stamp)
    component.add("sequence", event.sequence)
    component.add("summary", event.summary)
    component.add("description", event.description)
    component.add("location", event.location)
    if event.url:
        component.add("url", event.url)
    # Lets the agenda tell a calendar block from a meeting.
    if event.transparent:
        component.add("transp", "TRANSPARENT")
    component.add("x-google-event-type", event.event_type)
    for alarm in event.alarms:
        valarm = Alarm()
        valarm.add("action", "DISPLAY")
        valarm.add("description", alarm.description)
        valarm.add("trigger", alarm.trigger)
        component.add_component(valarm)
    return component


def write_calendar(events: list[IcalOutput]) -> bytes:
    """Render events as an iCalendar document, in the order given."""
    calendar = Calendar()
    calendar.add("prodid", "-//nixos//Google Calendar desktop sync//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "Google Calendar")
    for event in events:
        calendar.add_component(_event_component(event))
    return calendar.to_ical()
