"""Bar application: loads glue, runs it off the GUI thread, feeds QML."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import cast

from PySide6.QtCore import (
    Property,
    QFileSystemWatcher,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine, QQmlError

from talos_bar import glue, platform

QML_DIR = Path(__file__).parent / "qml"


class _Job(QRunnable):
    def __init__(self, bar: Bar, item: glue.Item, fn: glue.RefreshFn) -> None:
        super().__init__()
        self.bar, self.item, self.fn = bar, item, fn

    def run(self) -> None:
        try:
            out = self.fn(self.bar.ctx)
            self.bar.result.emit(self.item.id, out if out is not None else "", False)
        except Exception:
            traceback.print_exc()
            self.bar.result.emit(self.item.id, "", True)


class Bar(QObject):
    itemsChanged = Signal()
    result = Signal(str, object, bool)

    def __init__(self) -> None:
        super().__init__()
        self.ctx = glue.Context()
        self._items: dict[str, glue.Item] = {}
        self._view: dict[str, dict[str, object]] = {}
        self._timers: dict[str, QTimer] = {}
        self._pool = QThreadPool.globalInstance()
        self.result.connect(self._on_result)
        self._watch = QFileSystemWatcher(self)
        self._watch.directoryChanged.connect(self._changed)
        self._watch.fileChanged.connect(self._changed)
        self.reload()

    @Property(list, notify=itemsChanged)  # pyright: ignore[reportArgumentType]
    def items(self) -> list[dict[str, object]]:
        return [self._view[i] for i in self._items]

    @Slot(str)
    def _changed(self, _path: str) -> None:
        self.reload()

    @Slot()
    def reload(self) -> None:
        for t in self._timers.values():
            t.stop()
        self._timers.clear()
        self._items.clear()
        files: list[Path] = []
        for d in glue.glue_dirs():
            if d.is_dir():
                files += sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))
                self._watch.addPath(str(d))
        for f in files:
            self._watch.addPath(str(f))
            for it in glue.load_file(f):
                self._items[it.id] = it
                self._view.setdefault(it.id, {"id": it.id, "text": it.label, "tooltip": "", "error": False})
                self._schedule(it)
        self.itemsChanged.emit()

    def _schedule(self, it: glue.Item) -> None:
        if not it.refresh:
            return
        t = QTimer(self)
        t.setInterval(int(it.interval * 1000))
        refresh = it.refresh
        t.timeout.connect(lambda: self._pool.start(_Job(self, it, refresh)))
        t.start()
        self._timers[it.id] = t
        self._pool.start(_Job(self, it, refresh))

    @Slot(str, object, bool)
    def _on_result(self, item_id: str, out: object, error: bool) -> None:
        if item_id not in self._view:
            return
        v = self._view[item_id]
        v["error"] = error
        if isinstance(out, dict):
            rich = cast("glue.Rich", out)
            v["text"] = rich.get("text", str(v["text"]))
            v["tooltip"] = rich.get("tooltip", "")
        elif not error:
            v["text"] = str(out)
        self.itemsChanged.emit()

    @Slot(str)
    def click(self, item_id: str) -> None:
        it = self._items.get(item_id)
        if it is None or it.click is None:
            return
        on_click, refresh = it.click, it.refresh

        def clicked(ctx: glue.Context) -> glue.Output:
            on_click(ctx)
            return refresh(ctx) if refresh else None

        self._pool.start(_Job(self, it, clicked))


def _print_warnings(warnings: list[QQmlError]) -> None:
    for w in warnings:
        print(f"qml: {w.toString()}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    qml = QML_DIR / platform.prepare()
    app = QGuiApplication(argv or sys.argv)
    app.setApplicationName("talos-bar")
    bar = Bar()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bar", bar)
    engine.rootContext().setContextProperty("barAlign", os.environ.get("TALOS_BAR_ALIGN", "right"))
    engine.warnings.connect(_print_warnings)
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print(f"talos-bar: failed to load {qml}", file=sys.stderr)
        return 1
    root = engine.rootObjects()[0]
    assert isinstance(root, QWindow)
    platform.dock(root, height=36)
    return app.exec()
