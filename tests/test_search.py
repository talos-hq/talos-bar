"""The search client speaks talos-searchd's line protocol: subscribe, query, show events."""

import json
import os
from collections.abc import Callable
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from talos_bar import search


@pytest.fixture
def app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def spin_until(cond: Callable[[], bool], ms: int = 3000) -> bool:
    deadline = QDeadlineTimer(ms)
    while not cond() and not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    return cond()


def test_queries_and_show_events_round_trip(app: QCoreApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    path = f"talos-searchd-test-{os.getpid()}"
    QLocalServer.removeServer(path)
    monkeypatch.setenv("TALOS_SEARCHD_ENDPOINT", path)
    server = QLocalServer()
    assert server.listen(path)
    seen: list[str] = []
    clients: list[QLocalSocket] = []

    def accept() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            assert conn is not None
            clients.append(conn)

            def read(conn: QLocalSocket = conn) -> None:
                for line in bytes(conn.readAll().data()).splitlines():
                    req = cast("dict[str, str]", json.loads(line))
                    seen.append(req["op"])
                    if req["op"] == "subscribe":
                        conn.write(b'{"ok":true}\n{"event":"show"}\n')
                    elif req["op"] == "query":
                        hit = {
                            "id": "app:x",
                            "title": req["q"].upper(),
                            "subtitle": "",
                            "icon": None,
                            "score": 1,
                        }
                        conn.write(json.dumps({"hits": [hit]}).encode() + b"\n")

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    client = search.Search()
    shown: list[bool] = []
    client.show.connect(lambda: shown.append(True))

    assert spin_until(lambda: client.online and "subscribe" in seen and bool(shown))
    client.query("fire")

    def has_hit() -> bool:
        hits = cast("list[dict[str, object]]", client.hits)
        return bool(hits) and hits[0]["title"] == "FIRE"

    assert spin_until(has_hit)
    client.query("  ")
    assert client.hits == []


def test_push_to_talk_puts_the_transcript_in_dictated(
    app: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"talos-searchd-dictate-{os.getpid()}"
    QLocalServer.removeServer(path)
    monkeypatch.setenv("TALOS_SEARCHD_ENDPOINT", path)
    server = QLocalServer()
    assert server.listen(path)
    actions: list[str] = []
    clients: list[QLocalSocket] = []
    subscribers: list[QLocalSocket] = []

    def accept() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            assert conn is not None
            clients.append(conn)

            def read(conn: QLocalSocket = conn) -> None:
                for line in bytes(conn.readAll().data()).splitlines():
                    req = cast("dict[str, str]", json.loads(line))
                    if req["op"] == "subscribe":
                        subscribers.append(conn)
                        conn.write(b'{"ok":true}\n')
                    elif req["op"] == "dictate":
                        actions.append(req["action"])
                        conn.write(b'{"ok":true}\n')
                        if req["action"] == "stop":
                            for sub in subscribers:
                                sub.write(b'{"event":"dictation","text":"open firefox"}\n')

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    client = search.Search()
    heard: list[str] = []
    client.dictated.connect(heard.append)

    assert spin_until(lambda: client.online and bool(subscribers))
    client.dictate(True)
    assert client.listening
    client.dictate(False)
    assert not client.listening
    assert spin_until(lambda: heard == ["open firefox"])
    assert actions == ["start", "stop"]
