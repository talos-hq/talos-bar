"""Agenda popover survives refreshes and pinning; links in it can be copied (#56)."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from talos_bar import glue
from talos_bar.app import QML_DIR, Bar

RAILWAY = "https://grantsidea-production-caec.up.railway.app/"

HARNESS = b"""import QtQuick
import "../glue/builtin"
Window {
    width: 1000; height: 700; visible: true
    QtObject {
        id: bar
        property string openId: "agenda"
        property bool popupFocus: false
        function click(id) { popupFocus = true; }
        function close() { openId = ""; }
        function action(a, b, c) {}
    }
    Item {
        objectName: "host"
        width: 440; height: 600
        property real roomRight: 550
        property real roomLeft: 0
        property var content: ({})
        Agenda { objectName: "agenda"; width: 440; height: 600 }
    }
}"""


def wait_until(ready: Callable[[], bool], timeout_ms: int = 5000) -> None:
    waited = 0
    while not ready() and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20


def num(item: QQuickItem, name: str) -> float:
    return float(cast("float", item.property(name)))


def text(item: QQuickItem, name: str) -> str:
    return str(cast("str", item.property(name)))


def row(i: int, **extra: object) -> dict[str, object]:
    out: dict[str, object] = {
        "title": f"Meeting {i}",
        "time": f"{8 + i // 2}:{'30' if i % 2 else '00'} AM",
        "start": 1_800_000_000 + i * 1800,
        "end": 1_800_000_000 + i * 1800 + 1800,
        "status": "upcoming",
        "meeting": True,
        "location": "",
        "link_kind": "Zoom",
        "url": f"https://zoom.us/j/12345678{i:02d}?pwd=secret",
        "join_url": "",
        "details": f"Agenda for meeting {i}.\nSee it in action: {RAILWAY} and click Start.",
        "emails": [f"guest{i}@example.com"],
        "phones": [],
        "tomorrow": False,
        "payment_label": "",
        "payment_state": "",
    }
    out.update(extra)
    return out


def content(rows: list[dict[str, object]], countdown: str = "in 5 min") -> dict[str, object]:
    return {
        "events": rows,
        "done": [],
        "tomorrow": [],
        "next": rows[0],
        "countdown": countdown,
        "date": "Fri",
    }


class Harness:
    def __init__(self) -> None:
        self.app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
        self.engine = QQmlApplicationEngine()
        self.engine.loadData(HARNESS, QUrl.fromLocalFile(str(QML_DIR / "AgendaRefreshTest.qml")))
        assert self.engine.rootObjects()
        self.window = cast("QQuickWindow", self.engine.rootObjects()[0])
        assert QTest.qWaitForWindowExposed(self.window)
        self.window.requestActivate()
        self.host = self.find("host")
        self.agenda = self.find("agenda")

    def find(self, name: str) -> QQuickItem:
        found = self.window.findChild(QQuickItem, name)
        assert found is not None, name
        return found

    def set(self, value: dict[str, object]) -> None:
        self.host.setProperty("content", value)
        QTest.qWait(60)

    def find_visual(self, name: str) -> QQuickItem:
        """Repeater delegates are only in the visual tree, not the QObject tree."""
        todo = [self.window.contentItem()]
        while todo:
            item = todo.pop()
            if item.objectName() == name and item.isVisible():
                return item
            todo.extend(item.childItems())
        raise AssertionError(name)

    def flick(self) -> QQuickItem:
        scroll = self.find("agendaList")
        flick = cast("object", scroll.property("contentItem"))
        assert isinstance(flick, QQuickItem)
        return flick

    def drag_select(self, editor: QQuickItem) -> None:
        # The details pane drops in (DropMotion): let it settle before dragging.
        wait_until(lambda: bool(text(editor, "text")) and editor.isVisible())
        QTest.qWait(400)
        start = editor.mapToScene(QPointF(2, 8)).toPoint()
        end = editor.mapToScene(QPointF(120, 8)).toPoint()
        QTest.mousePress(self.window, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.window, end, 40)
        QTest.mouseRelease(self.window, Qt.MouseButton.LeftButton, pos=end)


@pytest.fixture
def h() -> Harness:
    return Harness()


def test_refresh_keeps_list_scroll_and_rows(h: Harness) -> None:
    rows = [row(i) for i in range(30)]
    h.set(content(rows))
    flick = h.flick()
    wait_until(lambda: num(flick, "contentHeight") > flick.height() + 400)
    flick.setProperty("contentY", 400)
    QTest.qWait(30)
    # A refresh with the same rows (only the countdown moved) changes nothing.
    h.set(content([dict(r) for r in rows], countdown="in 4 min"))
    assert num(flick, "contentY") == pytest.approx(400)
    # A real change (one row became paid) keeps the reader's place too.
    changed = [dict(r) for r in rows]
    changed[3] = row(3, payment_label="$Paid", payment_state="paid")
    h.set(content(changed))
    wait_until(lambda: abs(num(flick, "contentY") - 400) < 1)
    assert num(flick, "contentY") == pytest.approx(400)


def test_selection_survives_refresh_and_pin(h: Harness) -> None:
    rows = [row(i) for i in range(3)]
    h.set(content(rows))
    h.agenda.setProperty("focusRow", h.agenda.property("events")[0])
    QTest.qWait(60)
    editor = h.find("agendaDetails")
    h.drag_select(editor)
    selected = text(editor, "selectedText")
    assert selected, "dragging must select detail text"
    # A refresh with equal rows keeps the selection.
    h.set(content([dict(r) for r in rows], countdown="in 3 min"))
    assert text(editor, "selectedText") == selected
    QTest.keyClick(h.window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert h.app.clipboard().text() == selected
    # The shown meeting follows the refresh (same meeting, new row object).
    assert "Meeting 0" in text(h.agenda, "detailHtml")


def test_urls_in_details_are_links_that_copy(h: Harness) -> None:
    h.set(content([row(0)]))
    h.agenda.setProperty("focusRow", h.agenda.property("events")[0])
    QTest.qWait(80)
    editor = h.find("agendaDetails")
    point = None
    for y in range(0, int(editor.height()), 3):
        for x in range(0, int(editor.width()), 3):
            if editor.linkAt(x, y) == RAILWAY:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                point = QPoint(x, y)
                break
        if point is not None:
            break
    assert point is not None, "the railway URL must be a link"
    QTest.mouseClick(h.window, Qt.MouseButton.RightButton, pos=editor.mapToScene(point).toPoint())
    QTest.qWait(100)
    menu = h.window.findChild(QObject, "agendaCopyMenu")
    assert menu is not None and menu.property("link") == RAILWAY
    action = h.window.findChild(QQuickItem, "agendaCopyLink")
    assert action is not None
    QTest.mouseClick(
        h.window,
        Qt.MouseButton.LeftButton,
        pos=action.mapToScene(QPointF(action.width() / 2, action.height() / 2)).toPoint(),
    )
    QTest.qWait(50)
    assert h.app.clipboard().text() == RAILWAY


def test_row_zoom_link_copies_on_right_click(h: Harness) -> None:
    h.set(content([row(0)]))
    QTest.qWait(60)
    label = h.find_visual("agendaRowZoom")
    centre = label.mapToScene(QPointF(label.width() / 4, label.height() / 2)).toPoint()
    QTest.mouseClick(h.window, Qt.MouseButton.RightButton, pos=centre)
    QTest.qWait(50)
    assert h.app.clipboard().text() == "https://zoom.us/j/1234567800?pwd=secret"


# ── Controller signals ───────────────────────────────────────────────────────────────────


@pytest.fixture
def bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bar:
    _ = QGuiApplication.instance() or QGuiApplication([])
    (tmp_path / "demo.py").write_text("""
from talos_bar.glue import item, popup
@item("agenda", label="Calendar")
def agenda(ctx): pass
@popup("agenda", hover=True)
def agenda_popup(ctx): return {"title": "Agenda", "rows": []}
""")
    monkeypatch.setattr(glue, "glue_dirs", lambda: [tmp_path])
    b = Bar()
    b._pool.waitForDone()  # pyright: ignore[reportPrivateUsage]
    return b


def test_pinning_does_not_resend_content(bar: Bar) -> None:
    bar.hoverOpen("agenda")
    assert bar._pool.waitForDone(2000)  # pyright: ignore[reportPrivateUsage]
    wait_until(lambda: bool(bar.popup))
    content_signals: list[int] = []
    focus_signals: list[int] = []
    bar.popupChanged.connect(lambda: content_signals.append(1))
    bar.focusChanged.connect(lambda: focus_signals.append(1))
    bar.click("agenda")
    assert bar.popupFocus
    assert focus_signals and not content_signals


def test_identical_refresh_does_not_emit(bar: Bar) -> None:
    bar.hoverOpen("agenda")
    assert bar._pool.waitForDone(2000)  # pyright: ignore[reportPrivateUsage]
    wait_until(lambda: bool(bar.popup))
    seen: list[int] = []
    bar.popupChanged.connect(lambda: seen.append(1))
    bar._set_popup("agenda", {"title": "Agenda", "rows": []}, False)  # pyright: ignore[reportPrivateUsage]
    assert not seen
    bar._set_popup("agenda", {"title": "Agenda", "rows": [{"text": "new"}]}, False)  # pyright: ignore[reportPrivateUsage]
    assert seen == [1]
