import QtQuick
import org.kde.layershell as LayerShell

// Bar docked to the top edge through wlr-layer-shell: the compositor keeps
// it above windows and reserves its height so maximized windows sit below.
Bar {
    LayerShell.Window.scope: "talos-bar"
    LayerShell.Window.wantsToBeOnActiveScreen: false
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    // barAlign: "left" | "center" | "right" (TALOS_BAR_ALIGN). Right keeps it
    // clear of the centred Plasma top panel while both run side by side.
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
        | (barAlign === "left" ? LayerShell.Window.AnchorLeft : 0)
        | (barAlign === "right" ? LayerShell.Window.AnchorRight : 0)
    LayerShell.Window.exclusionZone: height
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
}
