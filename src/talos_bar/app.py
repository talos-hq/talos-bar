"""Bar application: loads glue, runs it off the GUI thread, feeds QML."""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from PySide6.QtCore import (
    Property,
    QFileSystemWatcher,
    QObject,
    QRectF,
    QRunnable,
    QSize,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont, QGuiApplication, QIcon, QPixmap, QRegion, QWindow
from PySide6.QtQml import QQmlApplicationEngine, QQmlError, QQmlImageProviderBase
from PySide6.QtQuick import QQuickImageProvider

from talos_bar import glue, platform

QML_DIR = Path(__file__).parent / "qml"


class _Job(QRunnable):
    """Runs one glue call on the pool and posts its result back as `kind`."""

    def __init__(self, bar: Bar, item_id: str, kind: str, fn: Callable[[glue.Context], object]) -> None:
        super().__init__()
        self.bar, self.item_id, self.kind, self.fn = bar, item_id, kind, fn

    def run(self) -> None:
        try:
            out = self.fn(self.bar.ctx)
            self.bar.result.emit(self.item_id, self.kind, out if out is not None else "", False)
        except Exception:
            traceback.print_exc()
            self.bar.result.emit(self.item_id, self.kind, "", True)


def _blank_view(it: glue.Item) -> dict[str, object]:
    return {
        "id": it.id,
        "text": it.label,
        "subtext": "",
        "icon": "",
        "dot": "",
        "tooltip": "",
        "error": False,
        "plain": it.plain,
        "expandable": it.popup is not None,
        "hover": it.hover,
    }


class ItemView(QObject):
    """Keep each QML delegate alive while its displayed data refreshes."""

    changed = Signal()

    def __init__(self, value: dict[str, object], parent: QObject) -> None:
        super().__init__(parent)
        self.value = value

    @Property("QVariantMap", notify=changed)  # pyright: ignore[reportArgumentType]
    def content(self) -> dict[str, object]:
        return self.value


# Breeze names the glue uses, mapped to icons Adwaita and other non-KDE
# themes also have, for desktops without Breeze installed.
ICON_FALLBACKS = {
    "view-calendar-agenda": "x-office-calendar",
    "view-calendar-day": "x-office-calendar",
    "view-task": "emblem-default",
    "internet-services": "applications-internet",
}


class IconProvider(QQuickImageProvider):
    """``image://icon/<name>``: freedesktop theme icons for QML."""

    def __init__(self) -> None:
        super().__init__(QQmlImageProviderBase.ImageType.Pixmap)

    def requestPixmap(self, id: str, size: QSize, requestedSize: QSize) -> QPixmap:
        side = max(requestedSize.width(), requestedSize.height(), 16)
        icon = QIcon.fromTheme(id)
        if icon.isNull() and id in ICON_FALLBACKS:
            icon = QIcon.fromTheme(ICON_FALLBACKS[id])
        pix = icon.pixmap(side, side)
        size.setWidth(pix.width())
        size.setHeight(pix.height())
        return pix


def _kde_font() -> QFont | None:
    """The Plasma UI font from kdeglobals (``font=Family,10,...``), if set."""
    path = Path.home() / ".config" / "kdeglobals"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("font="):
            family, _, rest = line[5:].partition(",")
            size = rest.split(",", 1)[0]
            return QFont(family, int(size) if size.isdigit() else 10)
    return None


def _use_desktop_theme(app: QGuiApplication) -> None:
    # A bare Qt app on Plasma gets neither the UI font nor the icon theme;
    # take the same ones the panel uses, falling back quietly elsewhere.
    font = _kde_font()
    if font is not None:
        app.setFont(font)
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        QIcon.setThemeName("breeze-dark")
    # Without Breeze (GNOME, most other desktops) look in Adwaita next.
    if not QIcon.fallbackThemeName() or QIcon.fallbackThemeName() == "hicolor":
        QIcon.setFallbackThemeName("Adwaita")


class Bar(QObject):
    itemsChanged = Signal()
    popupChanged = Signal()
    result = Signal(str, str, object, bool)

    def __init__(self) -> None:
        super().__init__()
        self.ctx = glue.Context()
        self._items: dict[str, glue.Item] = {}
        self._view: dict[str, dict[str, object]] = {}
        self._item_views: dict[str, ItemView] = {}
        self._timers: dict[str, QTimer] = {}
        self._pool = QThreadPool.globalInstance()
        self._open = ""  # id of the item whose popup is showing
        # Only a popup the user clicked open may take the keyboard, as in
        # Plasma; one opened by hover (or by code) must never steal focus.
        self._focus = False
        self._popup: dict[str, object] = {}
        self.result.connect(self._on_result)
        self._watch = QFileSystemWatcher(self)
        self._watch.directoryChanged.connect(self._changed)
        self._watch.fileChanged.connect(self._changed)
        # Set by main(); reload() trims QML components nothing uses any more.
        self.engine: QQmlApplicationEngine | None = None
        # Bumped per reload: the engine caches components by URL, so a new
        # query string makes an edited popup's QML load fresh.
        self._generation = 0
        self.reload()

    @Property(list, notify=itemsChanged)  # pyright: ignore[reportArgumentType]
    def items(self) -> list[ItemView]:
        ordered = sorted(self._items.values(), key=lambda i: (i.order, i.id))
        return [self._item_views[i.id] for i in ordered]

    @Property(str, notify=popupChanged)  # pyright: ignore[reportArgumentType]
    def openId(self) -> str:
        return self._open

    @Property(bool, notify=popupChanged)  # pyright: ignore[reportArgumentType]
    def popupFocus(self) -> bool:
        return self._focus

    @Property("QVariantMap", notify=popupChanged)  # pyright: ignore[reportArgumentType]
    def popup(self) -> dict[str, object]:
        return self._popup

    @Slot(str)
    def _changed(self, _path: str) -> None:
        self.reload()

    @Slot()
    def reload(self) -> None:
        for t in self._timers.values():
            t.stop()
        self._timers.clear()
        self._items.clear()
        # Scripts are imported once and cached; popup QML is cached by URL.
        # Without dropping both, a reload keeps running the old code.
        glue.forget_scripts()
        self._generation += 1
        if self.engine is not None:
            self.engine.trimComponentCache()
        files: list[Path] = []
        for d in glue.glue_dirs():
            if d.is_dir():
                files += sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))
                self._watch.addPath(str(d))
                for qml in d.glob("*.qml"):
                    self._watch.addPath(str(qml))
        if glue.SCRIPTS.is_dir():
            for script in glue.SCRIPTS.glob("*.py"):
                self._watch.addPath(str(script))
        for f in files:
            self._watch.addPath(str(f))
            for it in glue.load_file(f):
                self._items[it.id] = it
                v = self._view.setdefault(it.id, _blank_view(it))
                v.update(plain=it.plain, expandable=it.popup is not None, hover=it.hover)
                if it.id not in self._item_views:
                    self._item_views[it.id] = ItemView(v, self)
                self._item_views[it.id].changed.emit()
                self._schedule(it)
        if self._open not in self._items:
            self.close()
        self.itemsChanged.emit()

    def _schedule(self, it: glue.Item) -> None:
        if not it.refresh:
            return
        t = QTimer(self)
        t.setInterval(int(it.interval * 1000))
        t.timeout.connect(lambda: self._tick(it))
        t.start()
        self._timers[it.id] = t
        self._tick(it)

    def _tick(self, it: glue.Item) -> None:
        if it.refresh:
            self._pool.start(_Job(self, it.id, "item", it.refresh))
        # An open popup follows its item's refresh interval.
        if it.popup and self._open == it.id:
            self._pool.start(_Job(self, it.id, "popup", it.popup))

    @Slot(str, str, object, bool)
    def _on_result(self, item_id: str, kind: str, out: object, error: bool) -> None:
        if item_id not in self._view:
            return
        if kind == "popup":
            if item_id == self._open:
                content = cast("Mapping[str, object]", out) if isinstance(out, dict) else None
                self._set_popup(item_id, content or {}, error)
            return
        v = self._view[item_id]
        v["error"] = error
        if isinstance(out, dict):
            rich = cast("glue.Rich", out)
            v["text"] = rich.get("text", str(v["text"]))
            v["subtext"] = rich.get("subtext", "")
            v["icon"] = rich.get("icon", "")
            v["dot"] = rich.get("dot", "")
            v["tooltip"] = rich.get("tooltip", "")
        elif not error:
            v["text"] = str(out)
        self._item_views[item_id].changed.emit()

    def _set_popup(self, item_id: str, content: Mapping[str, object], error: bool) -> None:
        it = self._items[item_id]
        component = str(content.get("component", "ListPopup.qml"))
        here = it.source.parent / component if it.source else None
        path = here if here and here.is_file() else QML_DIR / component
        url = QUrl.fromLocalFile(str(path))
        url.setQuery(f"v={self._generation}")
        self._popup = {**content, "component": url.toString(), "error": error}
        self.popupChanged.emit()

    @Slot(str)
    def click(self, item_id: str) -> None:
        it = self._items.get(item_id)
        if it is None:
            return
        if it.popup is not None:
            if self._open == item_id and not self._focus:
                # Clicking a hover-opened tab pins it and hands it the keyboard.
                self._focus = True
                self.popupChanged.emit()
            else:
                self.toggle(item_id, focus=True)
            return
        if it.click is None:
            return
        on_click, refresh = it.click, it.refresh

        def clicked(ctx: glue.Context) -> glue.Output:
            on_click(ctx)
            return refresh(ctx) if refresh else None

        self._pool.start(_Job(self, item_id, "item", clicked))

    def toggle(self, item_id: str, focus: bool = False) -> None:
        if self._open == item_id:
            self.close()
        else:
            self.open(item_id, focus)

    @Slot(str)
    def hoverOpen(self, item_id: str) -> None:
        self.open(item_id)

    def open(self, item_id: str, focus: bool = False) -> None:
        it = self._items.get(item_id)
        if it is None or it.popup is None or self._open == item_id:
            return
        self._open = item_id
        self._focus = focus
        self._popup = {}
        self.popupChanged.emit()
        self._pool.start(_Job(self, item_id, "popup", it.popup))

    @Slot()
    def close(self) -> None:
        if self._open:
            self._open = ""
            self._focus = False
            self.popupChanged.emit()

    @Slot(QWindow, list)
    def setInputRects(self, window: QWindow, rects: list[QRectF]) -> None:
        """Limit pointer input to the drawn popup parts on a screen-wide surface."""
        region = QRegion()
        for r in rects:
            region = region.united(r.toAlignedRect())
        # An empty QWindow mask means "reset to the whole window". While a
        # dropdown is fully clipped, an off-surface pixel keeps input empty.
        if region.isEmpty():
            region = QRegion(-1, -1, 1, 1)
        window.setMask(region)

    @Slot(str, str, str)
    def action(self, item_id: str, name: str, payload: str) -> None:
        """A popup's QML asks its glue to do something (save, join, ...)."""
        it = self._items.get(item_id)
        handler = it.actions.get(name) if it else None
        if it is None or handler is None:
            print(f"talos-bar: no action {name!r} on {item_id!r}", file=sys.stderr)
            return

        def run(ctx: glue.Context) -> glue.Output:
            handler(ctx, payload)
            return it.refresh(ctx) if it.refresh else None

        self._pool.start(_Job(self, item_id, "item", run))
        if it.popup and self._open == item_id:
            self._pool.start(_Job(self, item_id, "popup", it.popup))


def _print_warnings(warnings: list[QQmlError]) -> None:
    for w in warnings:
        print(f"qml: {w.toString()}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    qml = QML_DIR / platform.prepare()
    app = QGuiApplication(argv or sys.argv)
    app.setApplicationName("talos-bar")
    _use_desktop_theme(app)
    bar = Bar()
    engine = QQmlApplicationEngine()
    bar.engine = engine
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("bar", bar)
    engine.rootContext().setContextProperty("barAlign", os.environ.get("TALOS_BAR_ALIGN", "right"))
    engine.rootContext().setContextProperty("layerShell", qml.name == "WaylandBar.qml")
    engine.warnings.connect(_print_warnings)
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print(f"talos-bar: failed to load {qml}", file=sys.stderr)
        return 1
    root = engine.rootObjects()[0]
    assert isinstance(root, QWindow)
    platform.dock(root)
    return app.exec()
