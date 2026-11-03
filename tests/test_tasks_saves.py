"""Save ordering and the real editor's draft lifetime."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from talos_bar import glue
from talos_bar.app import Bar, ItemView, _blank_view, _Job  # pyright: ignore[reportPrivateUsage]


class ControlledPool:
    def __init__(self) -> None:
        self.jobs: list[_Job] = []

    def start(self, job: _Job) -> None:
        self.jobs.append(job)

    def finish(self, index: int = 0) -> None:
        self.jobs.pop(index).run()


@pytest.fixture
def editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Bar, ControlledPool, QObject]]:
    app = cast("QGuiApplication", QGuiApplication.instance() or QGuiApplication([]))
    monkeypatch.setattr(glue, "glue_dirs", lambda: list[Path]())
    bar = Bar()
    pool = ControlledPool()
    bar._pool = pool  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    disk = tmp_path / "TASKS.md"
    disk.write_text("original")
    source = Path(__file__).parents[1] / "src/talos_bar/glue/builtin/tasks.py"
    item = glue.Item(id="tasks", label="Tasks", source=source)
    item.popup = lambda ctx: {"component": "Tasks.qml", "text": disk.read_text(), "synced": True}
    def save(_ctx: glue.Context, payload: str) -> None:
        disk.write_text(payload)

    item.actions["save"] = save
    bar._items["tasks"] = item  # pyright: ignore[reportPrivateUsage]
    view = _blank_view(item)
    bar._view["tasks"] = view  # pyright: ignore[reportPrivateUsage]
    bar._item_views["tasks"] = ItemView(view, bar)  # pyright: ignore[reportPrivateUsage]
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bar", bar)
    engine.loadData(b'''
import QtQuick
Item {
    property var content: bar.popup
    Loader {
        property var content: parent.content
        source: content.component || ""
    }
}
''', QUrl.fromLocalFile(str(tmp_path / "Editor.qml")))
    bar.open("tasks", focus=True)
    pool.finish()
    app.processEvents()
    root = engine.rootObjects()[0]
    text = next(o for o in root.findChildren(QObject) if o.property("cursorPosition") is not None)
    yield bar, pool, text
    bar.close()
    while pool.jobs:
        pool.finish()
    import shiboken6

    shiboken6.delete(engine)
    shiboken6.delete(bar)
    app.processEvents()


def test_actions_are_serial_and_reject_pre_save_popup_reads(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    bar, pool, _ = editor
    item = bar._items["tasks"]  # pyright: ignore[reportPrivateUsage]
    # A periodic read already in flight must not replace the acknowledged edit.
    bar._tick(item)  # pyright: ignore[reportPrivateUsage]
    stale = pool.jobs.pop()
    bar.action("tasks", "save", "first")
    bar.action("tasks", "save", "second")
    assert len(pool.jobs) == 1, "Only one mutation per item may run at a time"
    pool.finish()
    assert len(pool.jobs) == 1
    pool.finish()
    while pool.jobs:
        pool.finish()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "second"
    # Deliver the old read after all saves, as a real slow worker may do.
    bar.result.emit(stale.item_id, stale.kind, {"text": "original"}, False)
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "second"


def test_newer_draft_survives_ack_and_reopening(editor: tuple[Bar, ControlledPool, QObject]) -> None:
    bar, pool, text = editor
    text.setProperty("text", "first")
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    # Trigger autosave without waiting for its timer.
    from PySide6.QtCore import QMetaObject

    assert QMetaObject.invokeMethod(body, "save")
    text.setProperty("text", "second")
    pool.finish()
    assert text.property("text") == "second"
    assert body.property("dirty")
    bar.close()
    bar.open("tasks", focus=True)
    # Close has handed off the newer draft; reopening reads must show it even
    # when the save has not reached the disk yet.
    for job in list(pool.jobs):
        if job.kind.startswith("popup"):
            pool.jobs.remove(job)
            job.run()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "second"
    while pool.jobs:
        pool.finish()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "second"
