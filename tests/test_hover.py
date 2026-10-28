"""Exercise the real QML with pointer events, including periodic glue refreshes."""

import os
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QJSValue, QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from talos_bar import glue
from talos_bar.app import QML_DIR, Bar, IconProvider

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
@popup("tasks")
def tasks_popup(ctx): return {"title": "Tasks", "rows": []}
""")
    (tmp_path / "clock.py").write_text((QML_DIR.parent / "glue/builtin/clock.py").read_text())
    monkeypatch.setattr(glue, "glue_dirs", lambda: [tmp_path])
    bar = Bar()
    bar._pool.waitForDone()  # pyright: ignore[reportPrivateUsage]
    app.processEvents()
    bar._items["clock"].source = QML_DIR.parent / "glue/builtin/clock.py"  # pyright: ignore[reportPrivateUsage]
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
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
    assert "date" in content and "local" in content
    assert len(cast("list[object]", content["rows"])) in (7, 8)
    assert positions[0][1] > 24
    assert all(p == positions[0] for p in positions), positions


@pytest.mark.parametrize("kind", ["clock", "agenda", "tasks", "tooltip"])
def test_all_cards_drop_from_the_bar_without_horizontal_motion(ui: UI, kind: str) -> None:
    _, bar, window = ui
    popup_name = "tooltipPopup" if kind == "tooltip" else "itemPopup"
    popup = window.findChild(QQuickWindow, popup_name)
    assert popup is not None
    popup.setProperty("spansScreen", True)
    frame = next(i for i in popup.findChildren(QQuickItem) if i.property("animating") is not None)
    # A fully clipped dropdown must pass pointer input through. Qt treats an
    # empty mask as the full window, so its explicit region must be off-surface.
    mask = popup.mask()
    assert not mask.isEmpty()
    assert mask.intersected(QRect(0, 0, popup.width(), popup.height())).isEmpty()
    samples: list[tuple[float, float, float, int, int]] = []

    def sample() -> None:
        if popup.isVisible() and frame.opacity() > 0:
            position = frame.mapToScene(QPointF(0, 0))
            if position.y() + frame.height() <= float(popup.property("anchorY")):
                return  # the surface is mapped, but the card is still fully clipped
            mask = popup.mask().boundingRect()
            samples.append((position.x(), position.y(), frame.height(), mask.y(), mask.bottom()))

    popup.frameSwapped.connect(sample)
    selected = tab(window, "tasks" if kind == "tooltip" else kind)
    if kind == "tooltip":
        bar._on_result("tasks", "item", {"tooltip": "Example task"}, False)  # pyright: ignore[reportPrivateUsage]
    QTest.mouseMove(window, selected.mapToScene(selected.boundingRect().center()).toPoint())
    if kind == "tasks":
        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            pos=selected.mapToScene(selected.boundingRect().center()).toPoint(),
        )
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(800 if kind == "tooltip" else 350)
    assert len(samples) >= 3
    # The card starts behind the bar and emerges downward from its bottom.
    bottom = float(popup.property("anchorY"))
    assert samples[0][1] < bottom - samples[0][2] * 0.3, samples
    assert abs(samples[-1][1] - bottom) < 0.1
    assert all(abs(s[0] - samples[-1][0]) < 0.1 for s in samples)
    assert all(b[1] >= a[1] - 0.1 for a, b in pairwise(samples))
    # Invisible space above and below the reveal never takes pointer input.
    assert all(s[3] >= bottom and s[4] <= s[1] + s[2] + 1 for s in samples)


def test_calendar_meeting_details_also_drop_vertically(ui: UI) -> None:
    _, bar, window = ui
    row: dict[str, object] = {
        "title": "Example meeting",
        "time": "10:00 AM",
        "status": "future",
        "tomorrow": False,
        "location": "",
        "link_kind": "",
        "url": "",
        "details": "Example details",
        "emails": [],
        "phones": [],
    }
    content: dict[str, object] = {
        "component": str(QML_DIR.parent / "glue/builtin/Agenda.qml"),
        "date": "Example day",
        "events": [row],
        "done": [],
        "tomorrow": [],
    }
    bar._items["agenda"].popup = lambda ctx: content  # pyright: ignore[reportPrivateUsage]
    popup = window.findChild(QQuickWindow, "itemPopup")
    assert popup is not None
    popup.setProperty("spansScreen", True)
    selected = tab(window, "agenda")
    QTest.mouseMove(window, selected.mapToScene(selected.boundingRect().center()).toPoint())
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(350)
    agenda = next(i for i in popup.findChildren(QQuickItem) if i.property("sideWidth") is not None)
    details = next(i for i in agenda.findChildren(QQuickItem) if i.property("onRight") is not None)
    samples: list[tuple[float, float]] = []

    def sample() -> None:
        if details.isVisible() and details.opacity() > 0:
            p = details.mapToScene(QPointF(0, 0))
            samples.append((p.x(), p.y()))

    popup.frameSwapped.connect(sample)
    agenda.setProperty("focusRow", row)
    QTest.qWait(350)
    bottom = float(popup.property("anchorY"))
    assert len(samples) >= 3
    assert samples[0][1] < bottom - details.height() * 0.3
    assert abs(samples[-1][1] - bottom) < 0.1
    assert all(abs(p[0] - samples[-1][0]) < 0.1 for p in samples)
    assert all(b[1] >= a[1] - 0.1 for a, b in pairwise(samples))


def test_detailed_clock_renders_and_ticks_without_reopening(ui: UI) -> None:
    _, bar, window = ui
    selected = tab(window, "clock")
    QTest.mouseMove(window, selected.mapToScene(selected.boundingRect().center()).toPoint())
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(300)
    popup = window.findChild(QQuickWindow, "itemPopup")
    assert popup is not None
    body = next(i for i in popup.findChildren(QQuickItem) if i.property("cityWidth") is not None)
    content = cast("dict[str, object]", bar.property("popup"))
    local = cast("dict[str, str]", content["local"])
    before = local["time"]

    def labels(item: QQuickItem) -> list[str]:
        text = item.property("text")
        result = [text] if isinstance(text, str) else []
        for child in item.childItems():
            result.extend(labels(child))
        return result

    rendered = labels(body)
    assert str(content["date"]) in rendered
    assert f"{local['city']}: {before}" in rendered
    remote_rows = cast("list[dict[str, str]]", content["rows"])
    assert all(f"{row['city']}:" in rendered for row in remote_rows)
    assert any(" • " in text for text in rendered)
    assert "KOrganizer" not in " ".join(rendered)
    QTest.qWait(1100)
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(30)
    refreshed = cast("dict[str, object]", bar.property("popup"))
    assert cast("dict[str, str]", refreshed["local"])["time"] != before
    assert bar.openId == "clock"
    assert next(i for i in popup.findChildren(QQuickItem) if i.property("cityWidth") is not None) is body
