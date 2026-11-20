"""Live booking payment status from the booking server, cached for the agenda.

A background thread holds ``GET /api/bar/stream`` (server-sent events) open. Each
``snapshot`` event carries the whole bookings document; it is kept in memory and written
atomically to ``~/.cache/talos-bar/bookings.json``, which ``scripts/calendar_agenda.py``
(and through it both bars) reads. While the stream is down the thread polls
``GET /api/bar/bookings`` every 30 s with ``If-None-Match``, and reconnects with exponential
backoff and jitter, sending ``Last-Event-ID``. A stream silent for 60 s (the server pings
every 20 s) is treated as dead.

Configuration: ``TALOS_BAR_BOOKINGS_URL`` (default https://www.zachvorhies.com) and the key
from ``TALOS_BAR_BOOKINGS_KEY``, the mode-600 file ``~/.config/talos-bar/bookings_key``, or
the Hermes keystore file ``~/.config/hermes/talos-bar.env`` (``TALOS_BAR_BOOKINGS_KEY=`` and
optionally ``TALOS_BAR_BOOKINGS_URL=``), in that order.

``status()`` says whether the feed is connected, so the bar can show "payments offline"
instead of silently showing no payment pills (talos-hq/talos-bar#54).
Without a key the feature is off. Booking contents and the key are never logged.

Run ``python -m talos_bar.booking_payments`` to keep the cache fresh without the Qt bar
(for the Rust bar).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import random
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.client import HTTPResponse
from pathlib import Path
from typing import cast

DEFAULT_URL = "https://www.zachvorhies.com"
STREAM_PATH = "/api/bar/stream"
BOOKINGS_PATH = "/api/bar/bookings"
IDLE_TIMEOUT = 60.0
POLL_INTERVAL = 30.0
BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0
# A feed with no successful contact for this long reads as offline.
STALE_AFTER = 300.0

Snapshot = dict[str, object]
Listener = Callable[[Snapshot], None]


def key_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "talos-bar" / "bookings_key"


def hermes_file() -> Path:
    """The Hermes keystore's env file for the bar (shared with the owner's agents)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "hermes" / "talos-bar.env"


def _private_text(path: Path) -> str | None:
    """The file's text, or None when missing or readable by group/others (like ssh)."""
    try:
        if path.stat().st_mode & 0o077:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _env_file(text: str) -> dict[str, str]:
    """KEY=value lines; blank lines, comments and surrounding quotes ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        out[name.strip()] = value.strip().strip("'\"")
    return out


def cache_file() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "talos-bar" / "bookings.json"


@dataclass(frozen=True)
class Config:
    base_url: str
    key: str
    cache: Path


def load_config(
    env: dict[str, str] | None = None, path: Path | None = None, hermes: Path | None = None
) -> Config | None:
    """The feed's settings, or None (feature off) when there is no usable key.

    A key file readable by group or others is ignored, like ssh does.
    """
    env = dict(os.environ) if env is None else env
    key = env.get("TALOS_BAR_BOOKINGS_KEY", "").strip()
    url = env.get("TALOS_BAR_BOOKINGS_URL", "")
    if not key:
        key = (_private_text(path or key_file()) or "").strip()
    if not key:
        keystore = _env_file(_private_text(hermes or hermes_file()) or "")
        key = keystore.get("TALOS_BAR_BOOKINGS_KEY", "").strip()
        url = url or keystore.get("TALOS_BAR_BOOKINGS_URL", "")
    if not key:
        return None
    return Config(base_url=(url or DEFAULT_URL).rstrip("/"), key=key, cache=cache_file())


# ── Server-sent events ───────────────────────────────────────────────────────────────────


@dataclass
class SseEvent:
    event: str = "message"
    data: str = ""
    id: str | None = None


@dataclass
class SseParser:
    """The text/event-stream line protocol (WHATWG HTML, 9.2.6), one line at a time."""

    last_id: str = ""
    retry_ms: int | None = None
    _event: str = ""
    _data: list[str] = field(default_factory=list[str])
    _id: str | None = None

    def feed(self, line: str) -> SseEvent | None:
        """Take one line (without its newline); return an event when a blank line ends one."""
        if line == "":
            if not self._data and self._id is None and not self._event:
                return None
            if self._id is not None:
                self.last_id = self._id
            event = SseEvent(self._event or "message", "\n".join(self._data), self._id)
            self._event, self._data, self._id = "", [], None
            # An event with no data line is not dispatched (the spec's rule).
            return event if event.data or event.event != "message" else None
        if line.startswith(":"):
            return None  # a comment: the server's keep-alive ping
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id" and "\0" not in value:
            self._id = value
        elif name == "retry" and value.isdigit():
            self.retry_ms = int(value)
        return None


def iter_lines(read_line: Callable[[], bytes]) -> Iterator[str]:
    """Decoded lines until EOF; CRLF, LF and CR endings all end a line."""
    pending = ""
    while True:
        raw = read_line()
        if not raw:
            return
        pending += raw.decode("utf-8", errors="replace")
        if not pending.endswith(("\n", "\r")):
            continue
        yield from pending.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:-1]
        pending = ""


# ── The feed ─────────────────────────────────────────────────────────────────────────────


def backoff(failures: int, base: float = BACKOFF_BASE, cap: float = BACKOFF_MAX) -> float:
    """Exponential delay before reconnect ``failures`` (1-based), with jitter in [50%, 100%]."""
    ceiling = min(cap, base * 2.0 ** max(0, failures - 1))
    return ceiling * (0.5 + random.random() / 2)


def write_atomic(path: Path, snapshot: Snapshot) -> None:
    """Replace ``path`` with the snapshot in one step; readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bookings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(snapshot, out)
        os.chmod(tmp, 0o600)  # attendee names and emails
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


Opener = Callable[[urllib.request.Request, float], HTTPResponse]


def _http_error(code: int) -> str:
    return "key_rejected" if code in (401, 403) else "unreachable"


def _urlopen(request: urllib.request.Request, timeout: float) -> HTTPResponse:
    return cast("HTTPResponse", urllib.request.urlopen(request, timeout=timeout))


class PaymentsFeed:
    """The stream client thread and the latest snapshot."""

    def __init__(
        self,
        config: Config,
        listener: Listener | None = None,
        *,
        opener: Opener = _urlopen,
        idle_timeout: float = IDLE_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
        backoff_base: float = BACKOFF_BASE,
        backoff_max: float = BACKOFF_MAX,
    ) -> None:
        self.config = config
        self.listener = listener
        self._open = opener
        self.idle_timeout = idle_timeout
        self.poll_interval = poll_interval
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.parser = SseParser()
        self.etag = ""
        self.version = ""
        self.snapshot: Snapshot | None = None
        self.failures = 0
        self.last_poll = float("-inf")
        # Connection health for status(): when a snapshot last arrived or was confirmed
        # unchanged, and why the last attempt failed ("key_rejected" / "unreachable").
        self.last_ok = float("-inf")
        self.error = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # Public

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self.run, name="booking-payments", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> Snapshot | None:
        with self._lock:
            return self.snapshot

    def run(self) -> None:
        while not self._stop.is_set():
            # Never log: errors can carry the URL and headers.
            # http.client.HTTPException covers IncompleteRead: a server restart cuts
            # the chunked stream mid-read, and that must reconnect, not end the thread.
            try:
                self.stream_once()
            except urllib.error.HTTPError as err:
                err.close()
                self.error = _http_error(err.code)
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
                self.error = "unreachable"
            if self._stop.is_set():
                return
            self.failures += 1
            self.wait_down(self.reconnect_delay())

    # Steps, separately testable

    def reconnect_delay(self) -> float:
        delay = backoff(self.failures, self.backoff_base, self.backoff_max)
        if self.parser.retry_ms is not None:
            delay = max(delay, self.parser.retry_ms / 1000)
        return delay

    def wait_down(self, delay: float) -> None:
        """While the stream is down: poll now if due, then every poll_interval, until ``delay``."""
        deadline = time.monotonic() + delay
        while not self._stop.is_set():
            if time.monotonic() - self.last_poll >= self.poll_interval:
                self.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            next_poll = self.last_poll + self.poll_interval - time.monotonic()
            self._stop.wait(max(0.0, min(remaining, next_poll)))

    def _request(self, path: str, headers: dict[str, str]) -> urllib.request.Request:
        request = urllib.request.Request(self.config.base_url + path)
        request.add_header("Authorization", f"Bearer {self.config.key}")
        for name, value in headers.items():
            request.add_header(name, value)
        return request

    def stream_once(self) -> None:
        """Hold the stream until it ends or goes silent for idle_timeout (raises then)."""
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if self.parser.last_id:
            headers["Last-Event-ID"] = self.parser.last_id
        with self._open(self._request(STREAM_PATH, headers), self.idle_timeout) as response:
            if response.status != 200:
                raise ValueError("stream refused")
            self.failures = 0
            self.mark_ok()
            # The socket timeout bounds every read, so a silent stream raises TimeoutError.
            for line in iter_lines(response.readline):
                if self._stop.is_set():
                    return
                event = self.parser.feed(line)
                if event is not None and event.event in ("snapshot", "message"):
                    self.accept(cast("object", json.loads(event.data)), event.id or "")

    def poll(self) -> bool:
        """One ``GET /api/bar/bookings``; True when it brought a new snapshot."""
        self.last_poll = time.monotonic()
        headers = {"Accept": "application/json"}
        tag = self.etag or (f'"{self.version}"' if self.version else "")
        if tag:
            headers["If-None-Match"] = tag
        try:
            with self._open(self._request(BOOKINGS_PATH, headers), self.idle_timeout) as response:
                if response.status != 200:
                    return False
                self.etag = response.headers.get("ETag", "") or ""
                body = cast("object", json.loads(response.read()))
        except urllib.error.HTTPError as err:
            err.close()
            if err.code == 304:  # Not Modified: the feed is healthy
                self.mark_ok()
            else:
                self.error = _http_error(err.code)
            return False
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            self.error = "unreachable"
            return False
        self.mark_ok()
        if not isinstance(body, dict):
            return False
        return self.accept(cast("Snapshot", body), "")

    def mark_ok(self) -> None:
        self.last_ok = time.monotonic()
        self.error = ""

    def status(self, now: float | None = None) -> tuple[str, str]:
        """``(state, reason)``: ("ok", "") or ("offline", why) for the bar to show."""
        now = time.monotonic() if now is None else now
        if self.error == "key_rejected":
            return "offline", "key rejected"
        if now - self.last_ok <= STALE_AFTER:
            return "ok", ""
        if self.error:
            return "offline", "server unreachable"
        return "connecting", ""

    def accept(self, snapshot: object, event_id: str) -> bool:
        """Keep a snapshot, write the cache and tell the listener; False for a bad or stale one."""
        if not isinstance(snapshot, dict):
            return False
        snap = cast("Snapshot", snapshot)
        if not isinstance(snap.get("bookings"), list):
            return False
        version = str(snap.get("version") or event_id)
        with self._lock:
            if version and version == self.version and self.snapshot is not None:
                return False
            self.snapshot, self.version = snap, version
        if version and not self.parser.last_id:
            self.parser.last_id = version
        with contextlib.suppress(OSError):
            write_atomic(self.config.cache, snap)
        if self.listener is not None:
            # A listener bug must not kill the stream.
            with contextlib.suppress(Exception):
                self.listener(snap)
        return True


# ── One feed per process ─────────────────────────────────────────────────────────────────

_feed: PaymentsFeed | None = None
_listeners: dict[str, Listener] = {}


def _notify(snapshot: Snapshot) -> None:
    for listener in list(_listeners.values()):
        listener(snapshot)


def ensure_started(name: str = "", listener: Listener | None = None) -> PaymentsFeed | None:
    """Start the process-wide feed once (None when not configured).

    ``listener`` is registered under ``name``, replacing an earlier one of that name, so a
    reloaded glue module does not pile up callbacks.
    """
    global _feed
    if listener is not None:
        _listeners[name] = listener
    if _feed is None:
        config = load_config()
        if config is None:
            return None
        _feed = PaymentsFeed(config, _notify)
        _feed.start()
    return _feed


def status() -> tuple[str, str]:
    """``(state, reason)`` of the process-wide feed: "ok", "connecting" or "offline".

    "offline" carries why: "no key" (nothing configured), "key rejected" (401/403) or
    "server unreachable" (no contact for STALE_AFTER seconds).
    """
    if _feed is None:
        return ("offline", "no key") if load_config() is None else ("connecting", "")
    return _feed.status()


def main() -> int:
    config = load_config()
    if config is None:
        print(
            "booking payments: no key; set TALOS_BAR_BOOKINGS_KEY, ~/.config/talos-bar/bookings_key"
            " or ~/.config/hermes/talos-bar.env"
        )
        return 1
    feed = PaymentsFeed(config)
    try:
        feed.run()
    except KeyboardInterrupt:
        feed.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
