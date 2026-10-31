"""Search popup client: talks to talos-searchd over its local socket.

The daemon owns every OS touchpoint (hotkey, app catalog, launch) through kernal-api; this side
only draws. QLocalSocket speaks the same transport kernal-api's IPC binds: a Unix socket here,
a named pipe on Windows.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket


def endpoint() -> str:
    """Where talos-searchd listens by default (its `--print-endpoint`)."""
    if override := os.environ.get("TALOS_SEARCHD_ENDPOINT"):
        return override
    if sys.platform == "win32":
        return "talos-searchd"
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return str(Path(runtime) / "talos-searchd.sock")


class _Line(QObject):
    """One socket carrying newline-delimited JSON, reconnecting when the daemon restarts."""

    message = Signal(dict)
    connected = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.sock = QLocalSocket(self)
        self.buf = b""
        self.sock.readyRead.connect(self._read)
        self.sock.connected.connect(self.connected)
        self.sock.disconnected.connect(self._retry)
        self.sock.errorOccurred.connect(self._retry)
        self.retry = QTimer(self, singleShot=True, interval=2000)
        self.retry.timeout.connect(self.open)

    def open(self) -> None:
        if self.sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.sock.connectToServer(endpoint())

    def _retry(self, *_: object) -> None:
        if not self.retry.isActive():
            self.retry.start()

    def send(self, request: dict) -> bool:
        if self.sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            self.open()
            return False
        self.sock.write(json.dumps(request).encode() + b"\n")
        return True

    def _read(self) -> None:
        self.buf += bytes(self.sock.readAll().data())
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            try:
                self.message.emit(json.loads(line))
            except ValueError:
                continue


class Search(QObject):
    """Exposed to QML as `search`: query text in, hits out, `show` when the hotkey fires."""

    hitsChanged = Signal()
    show = Signal()
    onlineChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hits: list[dict] = []
        self._online = False
        self.requests = _Line(self)
        self.requests.message.connect(self._reply)
        self.requests.connected.connect(lambda: self._set_online(True))
        self.events = _Line(self)
        self.events.connected.connect(lambda: self.events.send({"op": "subscribe"}))
        self.events.message.connect(self._event)
        self.requests.open()
        self.events.open()

    def _set_online(self, online: bool) -> None:
        if online != self._online:
            self._online = online
            self.onlineChanged.emit()

    @Property(bool, notify=onlineChanged)  # pyright: ignore[reportArgumentType]
    def online(self) -> bool:
        return self._online

    @Property("QVariantList", notify=hitsChanged)  # pyright: ignore[reportArgumentType]
    def hits(self) -> list[dict]:
        return self._hits

    @Slot(str)
    def query(self, text: str) -> None:
        if not text.strip():
            self._hits = []
            self.hitsChanged.emit()
            return
        self.requests.send({"op": "query", "q": text})

    @Slot(str)
    def activate(self, hit_id: str) -> None:
        self.requests.send({"op": "activate", "id": hit_id})

    def _reply(self, msg: dict) -> None:
        if "hits" in msg:
            self._hits = msg["hits"]
            self.hitsChanged.emit()

    def _event(self, msg: dict) -> None:
        if msg.get("event") == "show":
            self.show.emit()
