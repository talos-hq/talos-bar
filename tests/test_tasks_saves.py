"""Save ordering and the real editor's draft lifetime."""

import json
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
        edit = cast("dict[str, str]", json.loads(payload)) if payload.startswith("{") else {"text": payload}
        disk.write_text(edit["text"])

    item.actions["save"] = save
    bar._items["tasks"] = item  # pyright: ignore[reportPrivateUsage]
    view = _blank_view(item)
    bar._view["tasks"] = view  # pyright: ignore[reportPrivateUsage]
    bar._item_views["tasks"] = ItemView(view, bar)  # pyright: ignore[reportPrivateUsage]
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bar", bar)
    engine.loadData(
        b"""
import QtQuick
Item {
    property var content: bar.popup
    Loader {
        property var content: parent.content
        source: content.component || ""
    }
}
""",
        QUrl.fromLocalFile(str(tmp_path / "Editor.qml")),
    )
    bar.open("tasks", focus=True)
    pool.finish()
    app.processEvents()
    root = engine.rootObjects()[0]
    bar.setProperty("testRoot", root)
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


def test_failed_draft_reopens_dirty_and_can_retry(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    item = bar._items["tasks"]  # pyright: ignore[reportPrivateUsage]
    save = item.actions["save"]

    def fail(_ctx: glue.Context, _payload: str) -> None:
        raise RuntimeError("save unavailable")

    item.actions["save"] = fail
    text.setProperty("text", "keep this draft")
    assert QMetaObject.invokeMethod(body, "save")
    pool.finish()
    assert body.property("dirty")
    assert body.property("saveError")
    bar.close()
    bar.open("tasks", focus=True)
    while pool.jobs:
        pool.finish()
    content = cast("dict[str, object]", bar.property("popup"))
    assert content["text"] == "keep this draft"
    assert content["editorDirty"] and content["saveError"]
    item.actions["save"] = save
    # The popup loader is recreated by close/reopen; retrieve its new editor.
    app = QGuiApplication.instance()
    assert app is not None
    app.processEvents()
    # Explicit retry against the real bar action is also what Retry save invokes.
    payload = json.dumps({"text": content["text"], "base": content["baseText"]})
    bar.action("tasks", "save", payload)
    while pool.jobs:
        pool.finish()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "keep this draft"
    assert not cast("dict[str, object]", bar.property("popup")).get("editorDirty", False)


def test_retyping_an_older_submission_keeps_new_revision_dirty(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    text.setProperty("text", "first")
    assert QMetaObject.invokeMethod(body, "save")
    text.setProperty("text", "second")
    assert QMetaObject.invokeMethod(body, "save")
    text.setProperty("text", "first")
    pool.finish()
    assert body.property("dirty"), "An older acknowledgement cannot acknowledge a newer revision"
    bar.close()
    bar.open("tasks", focus=True)
    while pool.jobs:
        pool.finish()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "first"


def test_discard_failed_draft_explicitly_reloads_saved_version(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None

    def conflict(_ctx: glue.Context, _payload: str) -> None:
        raise RuntimeError("file changed")

    bar._items["tasks"].actions["save"] = conflict  # pyright: ignore[reportPrivateUsage]
    text.setProperty("text", "unsaved draft")
    assert QMetaObject.invokeMethod(body, "save")
    pool.finish()
    while pool.jobs:
        pool.finish()
    assert text.property("text") == "unsaved draft"
    assert body.property("saveError")
    body.setProperty("dirty", False)  # The explicit discard button does this.
    bar.discardDraft("tasks")
    while pool.jobs:
        pool.finish()
    assert text.property("text") == "original"
    assert not body.property("dirty")
    assert not body.property("saveError")


def test_returning_to_original_text_is_a_new_edit(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    text.setProperty("text", "first")
    assert QMetaObject.invokeMethod(body, "save")
    text.setProperty("text", "original")
    pool.finish()
    assert body.property("dirty")
    bar.close()
    bar.open("tasks", focus=True)
    while pool.jobs:
        pool.finish()
    assert cast("dict[str, object]", bar.property("popup"))["text"] == "original"


def test_reopened_editor_stays_saving_until_all_queued_saves_finish(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    root = cast("QObject", bar.property("testRoot"))
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    text.setProperty("text", "first")
    assert QMetaObject.invokeMethod(body, "save")
    text.setProperty("text", "second")
    assert QMetaObject.invokeMethod(body, "save")
    bar.close()
    QGuiApplication.processEvents()
    bar.open("tasks", focus=True)
    for job in list(pool.jobs):
        if job.kind.startswith("popup"):
            pool.jobs.remove(job)
            job.run()
    QGuiApplication.processEvents()
    # The Loader keeps the detached previous component until deferred deletion.
    editors = root.findChildren(QObject, "tasksEditor")
    assert editors
    text = editors[-1]
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    assert body.property("saving")
    pool.finish()
    assert body.property("saving"), "The first acknowledgement must not enable recovery during the next save"
    pool.finish()
    assert not body.property("saving")


def test_identical_queued_saves_each_acknowledge_their_own_revision(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    bar, pool, _ = editor
    revision = bar.setDraft("tasks", "duplicate", "original")
    payload = json.dumps({"text": "duplicate", "base": "original", "revision": revision})
    bar.action("tasks", "save", payload)
    bar.action("tasks", "save", payload)
    pool.finish()
    pool.finish()
    while pool.jobs:
        pool.finish()
    popup = cast("dict[str, object]", bar.property("popup"))
    assert popup["text"] == "duplicate"
    assert not popup.get("editorDirty"), "Both successful identical saves must clear the acknowledged draft"
    revision = bar.setDraft("tasks", "followup", "duplicate")
    bar.action("tasks", "save", json.dumps({"text": "followup", "base": "duplicate", "revision": revision}))
    while pool.jobs:
        pool.finish()
    popup = cast("dict[str, object]", bar.property("popup"))
    assert popup["text"] == "followup"
    assert not popup.get("editorDirty")


def test_duplicate_ack_keeps_draft_retryable_when_later_save_fails(
    editor: tuple[Bar, ControlledPool, QObject],
) -> None:
    from PySide6.QtCore import QMetaObject

    bar, pool, text = editor
    item = bar._items["tasks"]  # pyright: ignore[reportPrivateUsage]
    original = item.actions["save"]
    count = 0

    def fail_second(ctx: glue.Context, payload: str) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("second save failed")
        original(ctx, payload)

    item.actions["save"] = fail_second
    text.setProperty("text", "duplicate")
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    revision = cast("int", body.property("revision"))
    payload = json.dumps({"text": "duplicate", "base": "original", "revision": revision})
    bar.action("tasks", "save", payload)
    bar.action("tasks", "save", payload)
    pool.finish()
    assert body.property("dirty"), "A successful duplicate must keep the draft until all saves finish"
    pool.finish()
    while pool.jobs:
        pool.finish()
    root = cast("QObject", bar.property("testRoot"))
    text = root.findChildren(QObject, "tasksEditor")[-1]
    body = text.parent()
    while body is not None and body.property("dirty") is None:
        body = body.parent()
    assert body is not None
    assert body.property("dirty") and body.property("saveError")
    assert QMetaObject.invokeMethod(body, "save")
    while pool.jobs:
        pool.finish()
    popup = cast("dict[str, object]", bar.property("popup"))
    assert popup["text"] == "duplicate"
    assert not popup.get("editorDirty") and not popup.get("saveError")
