import QtQuick
import org.kde.layershell as LayerShell

// A screen-wide transparent layer surface just under the bar; pointer input
// is masked to the drawn frame (bar.setInputRect), so the rest of the strip
// passes clicks through.
Popup {
    spansScreen: true
    // KWin does not move this below the bar's exclusive zone (same client),
    // so the frame hangs at the bar's foot inside the surface.
    anchorY: Theme.barHeight
    LayerShell.Window.scope: "talos-bar-popup"
    LayerShell.Window.wantsToBeOnActiveScreen: false
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: 0
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityOnDemand
}
