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
    // The tab under the pointer. One shared flag would be cleared by the
    // tab being left after the next one reported it was entered.
    property Item hoveredTab: null
    readonly property bool tabsHovered: hoveredTab !== null

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
                model: modelData.content
                expanded: bar.openId === tab.model.id
                onExpandedChanged: if (expanded) root.openTab = tab
                onActivated: bar.click(tab.model.id)
                onHoveredChanged: {
                    if (hovered) root.hoveredTab = tab;
                    else if (root.hoveredTab === tab) root.hoveredTab = null;
                    root.tipCheck();
                    // Hover popups open on entry; with any popup open, moving
                    // across tabs switches to the hovered one, menubar-style.
                    if (hovered && tab.model.expandable && (tab.model.hover || bar.openId !== ""))
                        bar.hoverOpen(tab.model.id);
                    root.hoverCheck();
                }
            }
        }
    }

    // A popup the user did not click open (hover-opened, or switched to by
    // moving across tabs) closes once the pointer has left both tab and
    // popup; the delay lets it cross the gap between the two surfaces.
    function hoverCheck() {
        if (root.tabsHovered || popupLoader.item && popupLoader.item.hovered) closeTimer.stop();
        else closeTimer.restart();
    }
    Timer {
        id: closeTimer
        interval: 150
        onTriggered: {
            if (bar.openId !== "" && !bar.popupFocus && !root.tabsHovered
                && !(popupLoader.item && popupLoader.item.hovered)) bar.close();
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
            item.focusable = Qt.binding(() => bar.popupFocus);
            item.anchorX = Qt.binding(() => root.openTab ? root.screenX + root.openTab.x + row.x + root.openTab.width / 2 : 0);
            item.dismissed.connect(() => bar.close());
            item.hoveredChanged.connect(root.hoverCheck);
        }
    }

    // ── Tooltips ──
    // Drawn in their own overlay surface: the bar's surface is only as tall
    // as the bar, and Controls' in-window ToolTip neither fits it nor goes
    // away reliably when the pointer leaves for another output or window.
    property Item tipTab: null
    readonly property bool tipShown: tipTab !== null && bar.openId === ""
    function tipCheck() {
        const tab = root.hoveredTab;
        if (tab && tab.model.tooltip !== "" && bar.openId === "") {
            tipHideTimer.stop();
            if (tab !== root.tipTab) { root.tipTab = null; tipTimer.restart(); }
        } else if (root.tipTab && tipLoader.item && tipLoader.item.hovered) {
            tipHideTimer.stop();  // the pointer moved into the pop-in
        } else {
            tipTimer.stop();
            tipHideTimer.restart();
        }
    }
    function showTip(itemId) {
        for (let i = 0; i < row.children.length; i++) {
            const tab = row.children[i];
            if (tab.model && tab.model.id === itemId) root.tipTab = tab;
        }
    }
    Timer {
        id: tipTimer
        interval: 500
        onTriggered: root.tipTab = root.hoveredTab
    }
    // Same 150 ms handoff as popups, so the pointer can reach the pop-in.
    Timer {
        id: tipHideTimer
        interval: 150
        onTriggered: if (!root.hoveredTab && !(tipLoader.item && tipLoader.item.hovered)) root.tipTab = null
    }
    Loader {
        id: tipLoader
        source: layerShell ? "WaylandPopup.qml" : "Popup.qml"
        onLoaded: {
            item.screen = Qt.binding(() => root.screen);
            item.shown = Qt.binding(() => root.tipShown);
            item.itemId = Qt.binding(() => root.tipTab ? root.tipTab.model.id : "");
            item.content = Qt.binding(() => ({
                component: Qt.resolvedUrl("TipBody.qml"),
                text: root.tipTab ? root.tipTab.model.tooltip : "",
                clickable: !!(root.tipTab && root.tipTab.model.expandable)
            }));
            item.hoveredChanged.connect(root.tipCheck);
            item.anchorX = Qt.binding(() => root.tipTab ? root.screenX + root.tipTab.x + row.x + root.tipTab.width / 2 : 0);
        }
    }
}
