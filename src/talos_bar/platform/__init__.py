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

from PySide6.QtCore import QLibraryInfo, Qt
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


def target_screen() -> QScreen:
    """The screen named by TALOS_BAR_SCREEN (e.g. HDMI-A-1), else the primary.

    Qt on Wayland does not always learn the compositor's primary output: on
    KWin it can report whichever output was announced first, which puts the
    bar on a side monitor.
    """
    name = os.environ.get("TALOS_BAR_SCREEN", "")
    for screen in QGuiApplication.screens():
        if name and screen.name() == name:
            return screen
    return QGuiApplication.primaryScreen()


def dock(window: QWindow) -> None:
    screen = target_screen()
    window.setScreen(screen)
    if QGuiApplication.platformName() == "wayland" and use_layer_shell():
        # Qt re-derives the output from the window position at show time, and
        # (0, 0) may belong to another monitor; start at the target's origin.
        window.setPosition(screen.geometry().topLeft())
        window.show()
        return
    window.setFlags(
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
    )
    geo = screen.geometry()

    def place() -> None:
        window.setPosition(geo.x() + (geo.width() - window.width()) // 2, geo.y())

    def on_width(_w: int) -> None:
        place()

    window.widthChanged.connect(on_width)
    place()
    window.show()
