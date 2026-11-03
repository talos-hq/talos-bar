"""Explicit editor clicks acquire keyboard focus without hover stealing it."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from talos_bar import glue
from talos_bar.app import QML_DIR, Bar


@pytest.fixture
def popup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Bar, QQuickWindow, QQuickItem]]:
    app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
    source = QML_DIR.parent / "glue/builtin/tasks.py"
    (tmp_path / "tasks.py").write_text(
        "from talos_bar.glue import item, popup, action\n"
        '@item("tasks", label="Tasks")\n'
        "def tasks(ctx): pass\n"
        '@popup("tasks")\n'
        f'def editor(ctx): return {{"component": {str(source.parent / "Tasks.qml")!r}, '
        '"text": "original", "synced": True}\n'
        '@action("tasks", "save")\n'
        "def save(ctx, payload): pass\n"
    )
    monkeypatch.setattr(glue, "glue_dirs", lambda: [tmp_path])
    bar = Bar()
    engine = QQmlApplicationEngine()
    layer_shell = QGuiApplication.platformName() == "wayland"
    engine.rootContext().setContextProperty("bar", bar)
    engine.rootContext().setContextProperty("layerShell", layer_shell)
    component = "WaylandPopup" if layer_shell else "Popup"
    engine.loadData(
        f"""import QtQuick
{component} {{
    shown: bar.openId !== ""
    itemId: bar.openId
    content: bar.popup
    focusable: bar.popupFocus
}}
""".encode(),
        QUrl.fromLocalFile(str(QML_DIR / "FocusTest.qml")),
    )
    assert engine.rootObjects()
    window = cast("QQuickWindow", engine.rootObjects()[0])
    bar.hoverOpen("tasks")
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    QTest.qWait(300)
    text = window.findChild(QQuickItem, "tasksEditor")
    assert text is not None
    yield bar, window, text
    bar.close()
    assert bar._pool.waitForDone(1000)  # pyright: ignore[reportPrivateUsage]
    import shiboken6

    shiboken6.delete(engine)
    shiboken6.delete(bar)
    app.processEvents()


def test_clicking_hover_open_editor_pins_and_accepts_typing(
    popup: tuple[Bar, QQuickWindow, QQuickItem],
) -> None:
    bar, window, text = popup
    assert window.isVisible()
    assert not bar.popupFocus, "Hover alone must not acquire keyboard focus"
    if QGuiApplication.platformName() != "wayland":
        assert window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
    # The text area can exceed the viewport: click near its visible top left.
    position = text.mapToScene(QPointF(24, 24)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=position)
    QTest.qWait(100)
    assert bar.popupFocus, "An explicit editor click must pin a hover-opened Tasks popup"
    assert bar.openId == "tasks"
    if QGuiApplication.platformName() != "wayland":
        assert not window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
    text = window.findChild(QQuickItem, "tasksEditor")
    assert text is not None
    before = cast("str", text.property("text"))
    QTest.keyClick(window, Qt.Key.Key_End)
    for key in " edited":
        QTest.keyClick(window, Qt.Key(ord(key.upper())))
    assert text.property("text") == before + " edited"
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=position)
    QTest.keyClick(window, Qt.Key.Key_End)
    for key in " again":
        QTest.keyClick(window, Qt.Key(ord(key.upper())))
    assert bar.openId == "tasks", "Repeated editor clicks must not toggle the popup closed"
    assert text.property("text") == before + " edited again"
