import QtQuick
import org.kde.layershell as LayerShell

// The search popup as a layer-shell surface: talos-bar's Wayland mode makes every window
// one, so the compositor centres it (no anchors) and hands it the keyboard while open.
SearchPopup {
    positioned: false
    LayerShell.Window.scope: "talos-search"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: 0
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
}
