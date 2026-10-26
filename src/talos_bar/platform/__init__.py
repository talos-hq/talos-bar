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

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QWindow


def use_layer_shell() -> bool:
    """Decide before QGuiApplication exists: Qt reads the shell integration at startup."""
    return (
        sys.platform == "linux"
        and bool(os.environ.get("WAYLAND_DISPLAY"))
        and os.environ.get("QT_QPA_PLATFORM", "wayland").startswith("wayland")
        and os.environ.get("TALOS_BAR_LAYER_SHELL", "1") != "0"
    )


def prepare() -> str:
    """Set process-wide Qt options; return the root QML file name to load."""
    # Qt on KDE sends QML console output to the journal; keep it on stderr.
    os.environ.setdefault("QT_FORCE_STDERR_LOGGING", "1")
    if use_layer_shell():
        os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
        return "WaylandBar.qml"
    return "Bar.qml"


def dock(window: QWindow) -> None:
    screen = QGuiApplication.primaryScreen()
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
