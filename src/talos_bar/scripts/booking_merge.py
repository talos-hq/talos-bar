"""Booking payment status for the agenda, from the cache talos_bar.booking_payments writes.

Standard library only: calendar_agenda imports it as a sibling, and the Rust bar runs that
script with a bare interpreter. Never logs booking contents.
"""

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# A calendar event and a booking are the same meeting when they start this close.
MATCH_WINDOW_SECONDS = 5 * 60
# Ignore a cache nothing has refreshed for this long: the feed is not running.
STALE_SECONDS = 24 * 60 * 60
CANCELLED = frozenset({"cancelled", "canceled", "rejected", "declined"})
PAID_LABEL = "$Paid"
UNPAID_LABEL = "unpaid"


def cache_path() -> Path:
    """``$XDG_CACHE_HOME/talos-bar/bookings.json`` (``~/.cache`` by default)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "talos-bar" / "bookings.json"


@dataclass(frozen=True, slots=True)
class Booking:
    """One booking from /api/bar/bookings, with the fields the agenda uses."""

    id: str
    start: float
    end: float
    name: str
    email: str
    event_type: str
    status: str
    label: str
    state: str


def _epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def payment_state(raw: dict[str, object]) -> tuple[str, str]:
    """Return ``(payment_label, payment_state)`` for one booking.

    ``paid: true`` (or the server's "$Paid" label) is "paid"; an "unpaid" label or
    payment_status is "unpaid"; anything else (waived, refunded, ...) is "other".
    """
    label = str(raw.get("payment_label") or "").strip()
    status = str(raw.get("payment_status") or "").strip().lower()
    if raw.get("paid") is True or label == PAID_LABEL:
        return PAID_LABEL, "paid"
    if label.lower() == UNPAID_LABEL or (not label and status == UNPAID_LABEL):
        return UNPAID_LABEL, "unpaid"
    return label or status, "other"


def parse_bookings(snapshot: object) -> list[Booking]:
    """The usable bookings in a snapshot; malformed entries are skipped."""
    if not isinstance(snapshot, dict):
        return []
    entries = snapshot.get("bookings")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(entries, list):
        return []
    out: list[Booking] = []
    for raw in entries:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(raw, dict):
            continue
        start, end = _epoch(raw.get("start_utc")), _epoch(raw.get("end_utc"))
        if start is None:
            continue
        status = str(raw.get("status") or "").lower()
        if status in CANCELLED:
            continue
        label, state = payment_state(raw)  # pyright: ignore[reportUnknownArgumentType]
        out.append(
            Booking(
                id=str(raw.get("id") or ""),
                start=start,
                end=end if end is not None and end > start else start + 1800,
                name=str(raw.get("name") or "").strip(),
                email=str(raw.get("email") or "").strip().lower(),
                event_type=str(raw.get("event_type") or "").strip(),
                status=status,
                label=label,
                state=state,
            )
        )
    return out


def load_bookings(path: Path | None = None, now: float | None = None) -> list[Booking]:
    """Bookings from the cache file; none when it is missing, unreadable or stale."""
    path = path or cache_path()
    try:
        if (now if now is not None else time.time()) - path.stat().st_mtime > STALE_SECONDS:
            return []
        return parse_bookings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def match(bookings: list[Booking], start: float, emails: list[str], title: str) -> Booking | None:
    """The booking for an event starting at ``start``: by attendee email first, then by name.

    Both need the start within MATCH_WINDOW_SECONDS; the closest start wins.
    """
    near = [b for b in bookings if abs(b.start - start) <= MATCH_WINDOW_SECONDS]
    lowered = {e.lower() for e in emails}
    by_email = [b for b in near if b.email and b.email in lowered]
    folded = title.casefold()
    pool = by_email or [b for b in near if b.name and b.name.casefold() in folded]
    return min(pool, key=lambda b: abs(b.start - start), default=None)


def booking_title(booking: Booking) -> str:
    """The title a booking missing from the calendar is shown under."""
    who = booking.name or booking.email or "Booking"
    return f"{booking.event_type}: {who}" if booking.event_type else who
