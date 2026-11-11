"""Exercise calendar selection and link copying with real Qt input and clipboard."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from talos_bar.app import QML_DIR


def test_calendar_copy() -> None:
    app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
    engine = QQmlApplicationEngine()
    engine.loadData(
        b"""import QtQuick
import "../glue/builtin"
Window {
    width: 1000; height: 700; visible: true
    QtObject {
        id: bar
        property string openId: "agenda"
        property bool popupFocus: false
        function click(id) { popupFocus = true; }
        function close() { openId = ""; }
    }
    Item {
        width: 440; height: 600
        property real roomRight: 550
        property real roomLeft: 0
        property var content: ({events: [row], next: row})
        property var row: ({title: "Planning & review", time: "10:00 AM", location: "Room A",
            link_kind: "", emails: ["person@example.com"], phones: ["+1 555 123 4567"],
            details: "First line\\nSecond line", url: "", join_url: ""})
        Agenda { id: agenda; objectName: "agenda"; width: 440; height: 600; focusRow: parent.row }
    }
}""",
        QUrl.fromLocalFile(str(QML_DIR / "AgendaCopyTest.qml")),
    )
    assert engine.rootObjects()
    window = cast("QQuickWindow", engine.rootObjects()[0])
    assert QTest.qWaitForWindowExposed(window)
    window.requestActivate()
    QTest.qWait(400)
    editor = window.findChild(QQuickItem, "agendaDetails")
    assert editor is not None
    start = editor.mapToScene(QPointF(2, 8)).toPoint()
    end = editor.mapToScene(QPointF(100, 8)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window, end, 40)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=end)
    assert editor.property("selectedText"), "Dragging must select detail text"
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert app.clipboard().text() == editor.property("selectedText")
    # Locate an actual rendered mailto anchor, then right-click it.
    link_point = None
    for y in range(0, int(editor.height()), 3):
        for x in range(0, int(editor.width()), 3):
            if editor.linkAt(x, y) == "mailto:person@example.com":  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                link_point = QPoint(x, y)
                break
        if link_point is not None:
            break
    assert link_point is not None
    QTest.mouseClick(window, Qt.MouseButton.RightButton, pos=editor.mapToScene(link_point).toPoint())
    QTest.qWait(100)
    menu = window.findChild(QObject, "agendaCopyMenu")
    assert menu is not None and menu.property("visible")
    assert menu.property("link") == "mailto:person@example.com"
    action = window.findChild(QObject, "agendaCopyLink")
    assert action is not None
    assert isinstance(action, QQuickItem)
    action_point = action.mapToScene(QPointF(action.width() / 2, action.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=action_point)
    assert app.clipboard().text() == "mailto:person@example.com"
    assert editor.property("readOnly")
    import shiboken6

    shiboken6.delete(engine)


def test_live_agenda_shows_current_and_next_as_distinct_rows() -> None:
    app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
    engine = QQmlApplicationEngine()
    engine.loadData(
        b"""import QtQuick
import "../glue/builtin"
Window {
    width: 440; height: 600; visible: true
    Item {
        width: parent.width; height: parent.height
        property real roomRight: 0
        property real roomLeft: 0
        property var current: row("Current call", "4:00 PM", "current", true)
        property var content: ({
            live: true, next: current,
            events: [current, row("Focus block", "4:45 PM", "upcoming", false),
                     row("Next call", "5:00 PM", "upcoming", true),
                     row("Later call", "6:00 PM", "upcoming", true)]
        })
        function row(title, time, status, meeting) {
            return {title, time, status, meeting, tomorrow: false, link_kind: meeting ? "Zoom" : "",
                    url: "https://example.zoom.us/j/12345678901", join_url: "", location: "",
                    emails: meeting ? ["guest@example.com"] : [], phones: [], details: ""};
        }
        Agenda { width: parent.width; height: implicitHeight }
    }
}""",
        QUrl.fromLocalFile(str(QML_DIR / "AgendaLiveTest.qml")),
    )
    assert engine.rootObjects()
    window = cast("QQuickWindow", engine.rootObjects()[0])
    assert QTest.qWaitForWindowExposed(window)

    def labels(item: QQuickItem) -> list[str]:
        if not item.isVisible():
            return []
        label = cast("object", item.property("text"))
        own = [label] if isinstance(label, str) else []
        return own + [value for child in item.childItems() for value in labels(child)]

    visible = labels(window.contentItem())
    assert visible.count("Current call") == 1
    assert "NOW · 4:00 PM" in visible
    assert [label for label in visible if label.startswith("NEXT · ")] == ["NEXT · 5:00 PM"]
    assert visible.count("Join Zoom meeting now") == 1
    assert "guest@example.com" in visible
    window.close()
    app.processEvents()
