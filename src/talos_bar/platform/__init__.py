"""Edge docking. The spike only floats the bar at the top centre of the
primary screen; real space reservation (layer-shell, X11 strut, AppBar,
NSPanel) lands per OS as described in docs/DESIGN.md."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QWindow


def dock(window: QWindow, height: int) -> None:
    window.setFlags(
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
    )
    geo = QGuiApplication.primaryScreen().availableGeometry()

    def place() -> None:
        window.setY(geo.y())
        window.setX(geo.x() + (geo.width() - window.width()) // 2)

    def on_width(_w: int) -> None:
        place()

    window.widthChanged.connect(on_width)
    window.setHeight(height)
    place()
    window.show()
