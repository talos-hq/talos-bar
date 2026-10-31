import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// The search popup as a layer-shell surface: talos-bar's Wayland mode makes every window
// one: anchored to the active screen's top edge (centred horizontally), a quarter down,
// with the keyboard while open.
SearchPopup {
    positioned: false
    LayerShell.Window.scope: "talos-search"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
    LayerShell.Window.wantsToBeOnActiveScreen: true
    LayerShell.Window.exclusionZone: 0
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
}
