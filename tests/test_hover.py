"""Exercise the real QML with pointer events, including periodic glue refreshes."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QJSValue, QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from talos_bar import glue
from talos_bar.app import QML_DIR, Bar

UI = tuple[QGuiApplication, Bar, QQuickWindow]


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UI]:
    app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
    (tmp_path / "demo.py").write_text("""
from talos_bar.glue import item, popup
@item("agenda", label="Calendar")
def agenda(ctx): pass
@popup("agenda", hover=True)
def agenda_popup(ctx): return {"title": "Agenda", "rows": []}
@item("tasks", label="Tasks")
def tasks(ctx): pass
""")
    (tmp_path / "clock.py").write_text((QML_DIR.parent / "glue/builtin/clock.py").read_text())
    monkeypatch.setattr(glue, "glue_dirs", lambda: [tmp_path])
    bar = Bar()
    bar._pool.waitForDone()  # pyright: ignore[reportPrivateUsage]
    app.processEvents()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bar", bar)
    engine.rootContext().setContextProperty("barAlign", "right")
    engine.rootContext().setContextProperty("layerShell", False)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Bar.qml")))
    window = cast("QQuickWindow", engine.rootObjects()[0])
    window.show()
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.qWait(350)
    yield app, bar, window
    for timer in bar._timers.values():  # pyright: ignore[reportPrivateUsage]
        timer.stop()
    bar._pool.waitForDone()  # pyright: ignore[reportPrivateUsage]
    window.close()
    import shiboken6

    shiboken6.delete(engine)
    shiboken6.delete(bar)
    app.processEvents()


def tab(window: QQuickWindow, item_id: str) -> QQuickItem:
    def walk(item: QQuickItem) -> Iterator[QQuickItem]:
        yield item
        for child in item.childItems():
            yield from walk(child)

    for t in walk(window.contentItem()):
        model = t.property("model")
        if isinstance(model, QJSValue):
            model = model.toVariant()
        if isinstance(model, dict) and cast("dict[str, object]", model).get("id") == item_id:
            return t
    raise AssertionError(f"Missing tab {item_id}")


def test_refresh_keeps_hovered_tab_and_open_popup(ui: UI) -> None:
    _, bar, window = ui
    agenda = tab(window, "agenda")
    p = agenda.mapToScene(agenda.boundingRect().center()).toPoint()
    QTest.mouseMove(window, p)
    QTest.qWait(30)
    assert bar.openId == "agenda"
    assert not bar.popupFocus
    for n in range(3):
        bar._on_result(  # pyright: ignore[reportPrivateUsage]
            "tasks", "item", {"text": str(n)}, False
        )
        QTest.qWait(200)
        assert tab(window, "agenda") is agenda
        assert window.property("hoveredTab") is agenda
        assert bar.openId == "agenda"
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.qWait(200)
    assert bar.openId == ""


def test_tooltip_survives_refresh_and_accepts_pointer_handoff(ui: UI) -> None:
    _, bar, window = ui
    bar._on_result(  # pyright: ignore[reportPrivateUsage]
        "tasks", "item", {"text": "Tasks", "tooltip": "A task"}, False
    )
    tasks = tab(window, "tasks")
    QTest.mouseMove(window, tasks.mapToScene(tasks.boundingRect().center()).toPoint())
    QTest.qWait(550)
    assert window.property("tipShown")
    for n in range(3):
        bar._on_result(  # pyright: ignore[reportPrivateUsage]
            "agenda", "item", {"text": str(n)}, False
        )
        QTest.qWait(200)
        assert window.property("tipShown")
        assert window.property("tipTab") is tasks
    tip = next(w for w in window.findChildren(QWindow) if w.isVisible())
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.mouseMove(tip, QPoint(tip.width() // 2, tip.height() // 2))
    QTest.qWait(200)
    assert window.property("tipShown")
    QTest.mouseMove(tip, QPoint(1, 1))
    QTest.qWait(200)
    assert not window.property("tipShown")


def test_hover_popup_handoff_and_click_pin(ui: UI) -> None:
    _, bar, window = ui
    agenda = tab(window, "agenda")
    p = agenda.mapToScene(agenda.boundingRect().center()).toPoint()
    QTest.mouseMove(window, p)
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(50)
    assert bar.openId == "agenda", (bar.openId, bar.popup, agenda.property("hovered"))
    popup = window.findChild(QQuickWindow, "itemPopup")
    assert popup is not None
    assert popup.isVisible(), (
        bar.openId,
        bar.popup,
        [(w.objectName(), w.isVisible()) for w in QGuiApplication.topLevelWindows()],
    )
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.mouseMove(popup, QPoint(popup.width() // 2, popup.height() // 2))
    QTest.qWait(200)
    assert bar.openId == "agenda"
    assert not bar.popupFocus
    QTest.mouseMove(popup, QPoint(1, 1))
    QTest.mouseMove(window, p)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=p)
    QTest.qWait(30)
    assert bar.popupFocus
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.qWait(200)
    assert bar.openId == "agenda"
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=p)
    assert bar.openId == ""


def test_tooltip_arriving_under_stationary_pointer(ui: UI) -> None:
    _, bar, window = ui
    tasks = tab(window, "tasks")
    QTest.mouseMove(window, tasks.mapToScene(tasks.boundingRect().center()).toPoint())
    QTest.qWait(100)
    assert not window.property("tipShown")
    bar._on_result("tasks", "item", {"tooltip": "Loaded task"}, False)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(550)
    assert window.property("tipShown")
    bar._on_result("tasks", "item", {"tooltip": ""}, False)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(200)
    assert not window.property("tipShown")


@pytest.mark.parametrize("from_left", [True, False])
def test_world_clock_opens_on_hover_from_either_direction(ui: UI, from_left: bool) -> None:
    _, bar, window = ui
    clock = tab(window, "clock")
    center = clock.mapToScene(clock.boundingRect().center()).toPoint()
    edge = clock.mapToScene(clock.boundingRect().topLeft()).toPoint()
    start = QPoint(edge.x() - 1 if from_left else edge.x() + int(clock.width()) + 1, center.y())
    QTest.mouseMove(window, start)
    QTest.qWait(200)
    bar.close()
    QTest.mouseMove(window, center)
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(200)
    assert bar.openId == "clock"
    assert not bar.popupFocus
    QTest.qWait(1100)
    assert bar.openId == "clock"
    QTest.mouseMove(window, QPoint(1, window.height() - 1))
    QTest.qWait(200)
    assert bar.openId == ""


@pytest.mark.parametrize("click", [False, True])
def test_popup_waits_for_content_before_mapping(ui: UI, click: bool) -> None:
    from threading import Event

    _, bar, window = ui
    release = Event()
    original = bar._items["clock"].popup  # pyright: ignore[reportPrivateUsage]
    assert original is not None

    def delayed(ctx: glue.Context) -> dict[str, object]:
        assert release.wait(timeout=5)
        return original(ctx)

    bar._items["clock"].popup = delayed  # pyright: ignore[reportPrivateUsage]
    try:
        clock = tab(window, "clock")
        center = clock.mapToScene(clock.boundingRect().center()).toPoint()
        QTest.mouseMove(window, center)
        if click:
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        QTest.qWait(50)
        popups = window.findChildren(QQuickWindow)
        assert not any(p.isVisible() for p in popups), "An empty popup must not be shown"
    finally:
        release.set()
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(250)
    assert any(p.isVisible() for p in popups)
    assert bar.popupFocus is click


def test_world_clock_frame_does_not_move_sideways_while_appearing(ui: UI) -> None:
    _, bar, window = ui
    window.setX(window.screen().geometry().right() - window.width())
    popup = window.findChild(QQuickWindow, "itemPopup")
    assert popup is not None
    popup.setProperty("spansScreen", True)
    frame = next(i for i in popup.findChildren(QQuickItem) if i.property("animating") is not None)
    positions: list[tuple[float, float]] = []

    def sample() -> None:
        if popup.isVisible() and frame.opacity() > 0:
            positions.append((frame.x(), frame.width()))

    popup.frameSwapped.connect(sample)
    clock = tab(window, "clock")
    QTest.mouseMove(window, clock.mapToScene(clock.boundingRect().center()).toPoint())
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(300)
    assert positions, (bar.openId, bar.popup, clock.property("hovered"), popup.isVisible())
    content = cast("dict[str, object]", bar.property("popup"))
    assert content["title"] == "World clock"
    assert len(cast("list[object]", content["rows"])) == 8
    assert positions[0][1] > 24
    assert all(p == positions[0] for p in positions), positions
