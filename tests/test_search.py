"""The search client speaks talos-searchd's line protocol: subscribe, query, show events."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop  # noqa: E402
from PySide6.QtNetwork import QLocalServer  # noqa: E402

from talos_bar import search  # noqa: E402


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def spin_until(cond, ms=3000):
    deadline = QDeadlineTimer(ms)
    while not cond() and not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    return cond()


def test_queries_and_show_events_round_trip(app, monkeypatch):
    path = f"talos-searchd-test-{os.getpid()}"
    QLocalServer.removeServer(path)
    monkeypatch.setenv("TALOS_SEARCHD_ENDPOINT", path)
    server = QLocalServer()
    assert server.listen(path)
    seen, clients = [], []

    def accept():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            clients.append(conn)

            def read(conn=conn):
                for line in bytes(conn.readAll().data()).splitlines():
                    req = json.loads(line)
                    seen.append(req["op"])
                    if req["op"] == "subscribe":
                        conn.write(b'{"ok":true}\n{"event":"show"}\n')
                    elif req["op"] == "query":
                        hit = {"id": "app:x", "title": req["q"].upper(), "subtitle": "", "icon": None, "score": 1}
                        conn.write(json.dumps({"hits": [hit]}).encode() + b"\n")

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    client = search.Search()
    shown = []
    client.show.connect(lambda: shown.append(True))

    assert spin_until(lambda: client.online and "subscribe" in seen and shown)
    client.query("fire")
    assert spin_until(lambda: client.hits and client.hits[0]["title"] == "FIRE")
    client.query("  ")
    assert client.hits == []
