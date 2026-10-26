import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Window {
    id: root
    visible: false
    color: "transparent"
    // The window is larger than the strip by the shadow's reach; the
    // compositor still reserves only barHeight (see WaylandBar.qml).
    width: row.implicitWidth + 16 + Theme.shadowPad * 2
    height: Theme.barHeight + Theme.shadowPad

    // The bar's left edge relative to its screen. Layer surfaces have no
    // global position, so derive it from the alignment there.
    readonly property real screenX: layerShell
        ? (barAlign === "right" ? screen.width - width : barAlign === "left" ? 0 : (screen.width - width) / 2)
        : x - screen.virtualX

    Rectangle {
        id: panel
        x: Theme.shadowPad
        width: parent.width - Theme.shadowPad * 2
        height: Theme.barHeight
        radius: Theme.radius
        color: Qt.alpha(Theme.window, Theme.panelOpacity)
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: 0.45
            shadowBlur: 0.6
            shadowVerticalOffset: 2
        }
    }

    property Item openTab: null
    property bool tabsHovered: false

    Row {
        id: row
        x: Theme.shadowPad + 8
        height: Theme.barHeight
        spacing: 2

        Repeater {
            model: bar.items
            delegate: Tab {
                id: tab
                required property var modelData
                model: modelData
                expanded: bar.openId === modelData.id
                onExpandedChanged: if (expanded) root.openTab = tab
                onActivated: bar.click(modelData.id)
                onHoveredChanged: {
                    root.tabsHovered = hovered;
                    // Hover popups open on entry; with any popup open, moving
                    // across tabs switches to the hovered one, menubar-style.
                    if (hovered && modelData.expandable && (modelData.hover || bar.openId !== ""))
                        bar.open(modelData.id);
                    root.hoverCheck();
                }
            }
        }
    }

    // Hover popups close once the pointer has left both tab and popup; the
    // delay lets it cross the gap between the two surfaces.
    function hoverCheck() {
        if (root.tabsHovered || popupLoader.item && popupLoader.item.hovered) closeTimer.stop();
        else closeTimer.restart();
    }
    Timer {
        id: closeTimer
        interval: 150
        onTriggered: {
            const open = bar.items.find(i => i.id === bar.openId);
            if (open && open.hover && !root.tabsHovered && !(popupLoader.item && popupLoader.item.hovered)) bar.close();
        }
    }

    Loader {
        id: popupLoader
        source: layerShell ? "WaylandPopup.qml" : "Popup.qml"
        onLoaded: {
            item.screen = Qt.binding(() => root.screen);
            item.shown = Qt.binding(() => bar.openId !== "");
            item.itemId = Qt.binding(() => bar.openId);
            item.content = Qt.binding(() => bar.popup);
            item.anchorX = Qt.binding(() => root.openTab ? root.screenX + root.openTab.x + row.x + root.openTab.width / 2 : 0);
            item.dismissed.connect(() => bar.close());
            item.hoveredChanged.connect(root.hoverCheck);
        }
    }
}
