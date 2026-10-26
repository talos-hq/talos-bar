import QtQuick
import org.kde.layershell as LayerShell

// A screen-wide transparent overlay-layer surface (above windows, as
// Plasma's applet popups are); pointer input
// is masked to the drawn frame (bar.setInputRect), so the rest of the strip
// passes clicks through.
Popup {
    spansScreen: true
    // exclusionZone -1 pins the surface to the screen's top edge whatever
    // other panels reserve, so the frame hangs at the bar's foot inside it.
    anchorY: Theme.barHeight
    LayerShell.Window.scope: "talos-bar-popup"
    LayerShell.Window.wantsToBeOnActiveScreen: false
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: focusable
        ? LayerShell.Window.KeyboardInteractivityOnDemand
        : LayerShell.Window.KeyboardInteractivityNone
}
