"""Edge docking.

On Wayland compositors with wlr-layer-shell (KWin, Hyprland, Sway) the bar
uses layer-shell-qt through ``qml/WaylandBar.qml``, which anchors it and
reserves screen space. Everywhere else the spike floats a frameless window at
the top centre of the primary screen; X11 struts, the Windows AppBar and the
macOS NSPanel land per OS as described in docs/DESIGN.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Property, QLibraryInfo, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication, QScreen, QWindow


def _layer_shell_qt_installed() -> bool:
    """Whether layer-shell-qt's Qt plugin is on Qt's plugin path.

    PySide6 from PyPI does not ship it; without it the layer-shell
    integration and the org.kde.layershell QML import both fail to load.
    """
    dirs = [d for d in os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep) if d]
    dirs.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    return any(Path(d, "wayland-shell-integration", "liblayer-shell.so").is_file() for d in dirs)


def use_layer_shell() -> bool:
    """Decide before QGuiApplication exists: Qt reads the shell integration at startup."""
    # GNOME's compositor does not implement wlr-layer-shell.
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return (
        sys.platform == "linux"
        and bool(os.environ.get("WAYLAND_DISPLAY"))
        and os.environ.get("QT_QPA_PLATFORM", "wayland").startswith("wayland")
        and os.environ.get("TALOS_BAR_LAYER_SHELL", "1") != "0"
        and "GNOME" not in desktop
        and _layer_shell_qt_installed()
    )


def prepare() -> str:
    """Set process-wide Qt options; return the root QML file name to load."""
    # Qt on KDE sends QML console output to the journal; keep it on stderr.
    os.environ.setdefault("QT_FORCE_STDERR_LOGGING", "1")
    if use_layer_shell():
        os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
        return "WaylandBar.qml"
    return "Bar.qml"


def _state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "talos-bar" / "screen"


class Placement(QObject):
    """Which output the bar lives on: chosen by the user, and kept.

    The choice is an output name (HDMI-A-1) saved under XDG_STATE_HOME;
    TALOS_BAR_SCREEN is only the default until something is chosen. Qt's
    primary screen is a last resort, because on KWin it can name a side
    monitor. While the chosen output is gone (asleep, unplugged) the bar
    waits on another one and goes back as soon as it reappears -- a restart
    during monitor sleep used to strand it on a side monitor for good.
    """

    screensChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._window = QWindow()  # replaced by dock() once QML has loaded
        self._shown = False
        try:
            saved = _state_file().read_text().strip()
        except OSError:
            saved = ""
        self._wanted = saved or os.environ.get("TALOS_BAR_SCREEN", "")
        app = QGuiApplication.instance()
        assert isinstance(app, QGuiApplication)
        app.screenAdded.connect(self._check)

        # Re-check once Qt has finished re-homing windows off a removed output.
        def screen_removed(_screen: QScreen) -> None:
            QTimer.singleShot(0, self._check)

        app.screenRemoved.connect(screen_removed)
        app.primaryScreenChanged.connect(self._check)

    def target(self) -> QScreen:
        for screen in QGuiApplication.screens():
            if screen.name() == self._wanted:
                return screen
        return QGuiApplication.primaryScreen()

    def _check(self, *_args: object) -> None:
        self.screensChanged.emit()
        if self._shown and self._window.screen() is not self.target():
            self.place()

    def place(self) -> None:
        screen = self.target()
        window = self._window
        if self._shown:
            # A layer surface binds its output when it is mapped; remap to move.
            window.hide()
        self._shown = True
        window.setScreen(screen)
        if QGuiApplication.platformName() == "wayland" and use_layer_shell():
            # Qt re-derives the output from the window position at show time, and
            # (0, 0) may belong to another monitor; start at the target's origin.
            window.setPosition(screen.geometry().topLeft())
        else:
            self._centre()
        window.show()
        self.screensChanged.emit()

    def _centre(self) -> None:
        geo = self._window.screen().geometry()
        self._window.setPosition(geo.x() + (geo.width() - self._window.width()) // 2, geo.y())

    @Property(list, notify=screensChanged)  # pyright: ignore[reportArgumentType]
    def screens(self) -> list[dict[str, object]]:
        current = self._window.screen()
        return [
            {
                "name": s.name(),
                "label": f"{s.model() or s.name()}  ·  {s.name()}  ·  {s.size().width()}x{s.size().height()}",
                "current": s is current,
            }
            for s in QGuiApplication.screens()
        ]

    @Slot(str)
    def moveTo(self, name: str) -> None:
        self._wanted = name
        try:
            path = _state_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name + "\n")
        except OSError as e:
            print(f"talos-bar: cannot save the screen choice: {e}", file=sys.stderr)
        self.place()


def dock(window: QWindow, placement: Placement) -> None:
    placement._window = window  # pyright: ignore[reportPrivateUsage]
    if not (QGuiApplication.platformName() == "wayland" and use_layer_shell()):
        window.setFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )

        def width_changed(_width: int) -> None:
            placement._centre()  # pyright: ignore[reportPrivateUsage]

        window.widthChanged.connect(width_changed)
    placement.place()
