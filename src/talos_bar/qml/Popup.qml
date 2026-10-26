import QtQuick

// A popup under the open tab. This plain-window version positions itself in
// global coordinates; WaylandPopup.qml spans the screen width instead and
// places the frame under the tab inside its own surface.
Window {
    id: win
    property string itemId: ""
    property var content: ({})
    property bool shown: false
    // Tab centre and bar bottom, relative to the screen's top-left.
    property real anchorX: 0
    property real anchorY: Theme.barHeight
    property bool spansScreen: false
    readonly property bool hovered: frame.hovered
    readonly property int pad: Theme.shadowPad + 4
    signal dismissed()

    // Not a child of the bar: layer-shell cannot parent a popup to a layer
    // surface it did not create, and Qt would otherwise make it transient.
    transientParent: null
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
    color: "transparent"
    visible: shown || frame.animating
    width: spansScreen ? screen.width : frame.implicitWidth + pad * 2
    height: frame.implicitHeight + pad * 2 + (spansScreen ? anchorY : 0)
    // The position also picks the output when a layer surface is created.
    x: screen.virtualX + (spansScreen ? 0 : anchorX - width / 2)
    y: screen.virtualY + anchorY

    onActiveChanged: if (!active && shown) dismissed()
    onVisibleChanged: if (visible) requestActivate()

    PopupFrame {
        id: frame
        shown: win.shown
        itemId: win.itemId
        content: win.content
        y: win.spansScreen ? win.anchorY : win.pad - 4
        x: win.spansScreen
            ? Math.max(8, Math.min(win.anchorX - width / 2, win.width - width - 8))
            : win.pad
        width: implicitWidth
        height: implicitHeight
        onXChanged: win.updateMask()
        onWidthChanged: win.updateMask()
        onHeightChanged: win.updateMask()
        onYChanged: win.updateMask()
    }
    function updateMask() {
        if (spansScreen) bar.setInputRect(win, frame.x, frame.y, frame.width, frame.height);
    }
    Shortcut { sequence: "Escape"; onActivated: win.dismissed() }
}
