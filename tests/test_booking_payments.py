"""The booking payments feed: SSE parsing, reconnect, polling fallback, config, cache."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from talos_bar import booking_payments as bp
from talos_bar.booking_payments import Config, PaymentsFeed, SseParser, iter_lines


def _snap(version: str, label: str = "$Paid") -> dict[str, object]:
    return {
        "version": version,
        "generated_at": "2026-11-12T10:00:00Z",
        "bookings": [{"id": "b1", "start_utc": "2026-11-12T18:00:00Z", "payment_label": label}],
    }


# ── SSE parsing ──────────────────────────────────────────────────────────────────────────


def _parse(text: str) -> tuple[SseParser, list[bp.SseEvent]]:
    parser = SseParser()
    lines = iter(text.encode().splitlines(keepends=True))
    events = [e for line in iter_lines(lambda: next(lines, b"")) if (e := parser.feed(line))]
    return parser, events


def test_sse_snapshot_with_id_and_retry() -> None:
    parser, events = _parse('retry: 3000\nevent: snapshot\nid: v1\ndata: {"a": 1}\n\n')
    assert [(e.event, e.id, e.data) for e in events] == [("snapshot", "v1", '{"a": 1}')]
    assert parser.retry_ms == 3000
    assert parser.last_id == "v1"


def test_sse_pings_are_ignored() -> None:
    _, events = _parse(": ping\n\n: ping\n\nevent: snapshot\ndata: x\n\n: ping\n\n")
    assert [e.data for e in events] == ["x"]


def test_sse_multiline_data_joins_with_newlines_and_crlf() -> None:
    _, events = _parse('event: snapshot\r\ndata: {"a":\r\ndata:  1}\r\n\r\n')
    assert events[0].data == '{"a":\n 1}'
    assert json.loads(events[0].data) == {"a": 1}


def test_sse_event_without_data_is_dropped_but_id_kept() -> None:
    parser, events = _parse("id: v9\n\n")
    assert events == []
    assert parser.last_id == "v9"


def test_iter_lines_joins_partial_reads() -> None:
    chunks = iter([b"data: ab", b"c\n", b"\n"])
    assert list(iter_lines(lambda: next(chunks, b""))) == ["data: abc", ""]


# ── Against a local server ───────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    # Set per test.
    script: ClassVar[Callable[[Handler], None]]
    seen: ClassVar[list[dict[str, str]]] = []

    def do_GET(self) -> None:
        type(self).seen.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
        type(self).script(self)

    def log_message(self, format: str, *args: object) -> None:
        pass

    def send_sse(self, *events: str, hold: float = 0.0) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(event.encode())
            self.wfile.flush()
        time.sleep(hold)
        self.close_connection = True

    def send_json(self, body: dict[str, object], etag: str) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextmanager
def server(script: Callable[[Handler], None]) -> Generator[str]:
    Handler.script = script
    Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _feed(
    url: str,
    tmp_path: Path,
    *,
    idle_timeout: float = 60.0,
    poll_interval: float = 30.0,
    backoff_base: float = 1.0,
    backoff_max: float = 60.0,
) -> tuple[PaymentsFeed, list[dict[str, object]]]:
    got: list[dict[str, object]] = []
    config = Config(base_url=url, key="sekrit", cache=tmp_path / "c" / "bookings.json")
    feed = PaymentsFeed(
        config,
        got.append,
        idle_timeout=idle_timeout,
        poll_interval=poll_interval,
        backoff_base=backoff_base,
        backoff_max=backoff_max,
    )
    return feed, got


def _sse(version: str) -> str:
    return f"event: snapshot\nid: {version}\ndata: {json.dumps(_snap(version))}\n\n"


def test_stream_delivers_snapshot_and_writes_cache(tmp_path: Path) -> None:
    with server(lambda h: h.send_sse("retry: 3000\n\n", ": ping\n\n", _sse("v1"))) as url:
        feed, got = _feed(url, tmp_path)
        feed.stream_once()
    assert [g["version"] for g in got] == ["v1"]
    assert Handler.seen[0]["authorization"] == "Bearer sekrit"
    assert Handler.seen[0]["path"] == "/api/bar/stream"
    assert "last-event-id" not in Handler.seen[0]
    cache = tmp_path / "c" / "bookings.json"
    assert json.loads(cache.read_text())["version"] == "v1"
    assert cache.stat().st_mode & 0o777 == 0o600
    assert feed.parser.retry_ms == 3000


def test_reconnect_sends_last_event_id(tmp_path: Path) -> None:
    versions = iter(["v1", "v2"])
    with server(lambda h: h.send_sse(_sse(next(versions)))) as url:
        feed, got = _feed(url, tmp_path)
        feed.stream_once()
        feed.stream_once()
    assert [g["version"] for g in got] == ["v1", "v2"]
    assert Handler.seen[1]["last-event-id"] == "v1"


def test_run_loop_reconnects_after_the_stream_ends(tmp_path: Path) -> None:
    versions = iter(["v1", "v2", "v3", "v4", "v5"])

    def script(h: Handler) -> None:
        if h.path.startswith("/api/bar/bookings"):
            h.send_response(304)
            h.end_headers()
        else:
            h.send_sse(_sse(next(versions, "v5")))

    with server(script) as url:
        feed, got = _feed(url, tmp_path, backoff_base=0.01, backoff_max=0.02)
        feed.start()
        deadline = time.monotonic() + 5
        while len(got) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        feed.stop()
    assert [g["version"] for g in got[:3]] == ["v1", "v2", "v3"]
    streams = [s for s in Handler.seen if s["path"] == "/api/bar/stream"]
    assert streams[1]["last-event-id"] == "v1"
    assert streams[2]["last-event-id"] == "v2"


def test_run_loop_survives_a_chunked_stream_cut_mid_chunk(tmp_path: Path) -> None:
    """A server restart cuts the chunked stream mid-read (http.client.IncompleteRead,
    not an OSError); the thread must reconnect rather than die."""
    versions = iter(["v1", "v2", "v3"])

    def script(h: Handler) -> None:
        if h.path.startswith("/api/bar/bookings"):
            h.send_response(304)
            h.end_headers()
            return
        h.protocol_version = "HTTP/1.1"
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Transfer-Encoding", "chunked")
        h.end_headers()
        body = _sse(next(versions, "v3")).encode()
        h.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
        h.wfile.write(b"400\r\npartial")  # promises 1024 bytes, then the server dies
        h.wfile.flush()
        h.close_connection = True

    with server(script) as url:
        feed, got = _feed(url, tmp_path, backoff_base=0.01, backoff_max=0.02)
        feed.start()
        deadline = time.monotonic() + 5
        while len(got) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        alive = feed._thread is not None and feed._thread.is_alive()  # pyright: ignore[reportPrivateUsage]
        feed.stop()
    assert [g["version"] for g in got[:2]] == ["v1", "v2"]
    assert alive


def test_silent_stream_is_dead_after_idle_timeout(tmp_path: Path) -> None:
    with server(lambda h: h.send_sse(_sse("v1"), hold=1.0)) as url:
        feed, got = _feed(url, tmp_path, idle_timeout=0.2)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            feed.stream_once()
    assert time.monotonic() - started < 0.9
    assert [g["version"] for g in got] == ["v1"]


def test_polling_fallback_uses_if_none_match_and_honours_304(tmp_path: Path) -> None:
    def script(h: Handler) -> None:
        if h.headers.get("If-None-Match") == '"v7"':
            h.send_response(304)
            h.send_header("ETag", '"v7"')
            h.end_headers()
        else:
            h.send_json(_snap("v7"), '"v7"')

    with server(script) as url:
        feed, got = _feed(url, tmp_path)
        assert feed.poll() is True
        assert feed.poll() is False
    assert [s["path"] for s in Handler.seen] == ["/api/bar/bookings"] * 2
    assert "if-none-match" not in Handler.seen[0]
    assert Handler.seen[1]["if-none-match"] == '"v7"'
    assert [g["version"] for g in got] == ["v7"]
    assert feed.parser.last_id == "v7", "a later reconnect resumes from the polled version"


def test_stream_down_polls_until_reconnect(tmp_path: Path) -> None:
    def script(h: Handler) -> None:
        if h.path == "/api/bar/stream":
            h.send_response(503)
            h.end_headers()
        else:
            h.send_json(_snap("v3"), '"v3"')

    with server(script) as url:
        feed, got = _feed(url, tmp_path, poll_interval=0.05)
        with pytest.raises(OSError):
            feed.stream_once()
        feed.wait_down(0.18)
    polls = [s for s in Handler.seen if s["path"] == "/api/bar/bookings"]
    assert len(polls) >= 3
    assert [g["version"] for g in got] == ["v3"], "an unchanged version is not re-delivered"


def test_backoff_grows_caps_and_jitters() -> None:
    for failures, ceiling in [(1, 1.0), (2, 2.0), (4, 8.0), (20, 60.0)]:
        delays = [bp.backoff(failures) for _ in range(50)]
        assert all(ceiling / 2 <= d <= ceiling for d in delays)
        assert len(set(delays)) > 1


def test_server_retry_sets_the_minimum_delay(tmp_path: Path) -> None:
    feed, _ = _feed("http://x", tmp_path)
    feed.failures = 1
    feed.parser.retry_ms = 3000
    assert feed.reconnect_delay() >= 3.0


# ── Config ───────────────────────────────────────────────────────────────────────────────


def test_config_env_key_and_default_url() -> None:
    config = bp.load_config({"TALOS_BAR_BOOKINGS_KEY": "k"}, Path("/nonexistent"))
    assert config is not None
    assert (config.base_url, config.key) == ("https://www.zachvorhies.com", "k")


def test_config_key_file_must_be_private(tmp_path: Path) -> None:
    key = tmp_path / "bookings_key"
    key.write_text("filekey\n")
    key.chmod(0o644)
    assert bp.load_config({}, key) is None
    key.chmod(0o600)
    config = bp.load_config({"TALOS_BAR_BOOKINGS_URL": "http://h:1/"}, key)
    assert config is not None
    assert (config.base_url, config.key) == ("http://h:1", "filekey")


def test_no_key_turns_the_feature_off() -> None:
    assert bp.load_config({}, Path("/nonexistent")) is None
