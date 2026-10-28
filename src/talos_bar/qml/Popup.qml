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
    // Set only for a popup the user clicked open; see Bar.popupFocus.
    property bool focusable: false
    readonly property bool hovered: frame.hovered
    readonly property int pad: Theme.shadowPad + 4
    signal dismissed()

    // Not a child of the bar: layer-shell cannot parent a popup to a layer
    // surface it did not create, and Qt would otherwise make it transient.
    transientParent: null
    // Hover windows must not activate merely by mapping. Layer-shell also
    // enforces this through keyboardInteractivity; plain Qt windows need it too.
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        | (!layerShell && !focusable ? Qt.WindowDoesNotAcceptFocus : 0)
    color: "transparent"
    // Glue is asynchronous. Do not paint an empty frame, including while
    // switching tabs: its temporary 24px width would move the popup sideways.
    visible: !!content.component && (shown || frame.animating)
    width: spansScreen ? screen.width : frame.implicitWidth + pad * 2
    height: frame.implicitHeight + pad * 2 + (spansScreen ? anchorY : 0)
    // The position also picks the output when a layer surface is created.
    x: screen.virtualX + (spansScreen ? 0 : anchorX - width / 2)
    y: screen.virtualY + anchorY

    // A clicked-open popup holds the keyboard and closes when it loses it
    // (a click elsewhere), like a Plasma applet popup.
    onActiveChanged: if (!active && visible && content.component && shown && focusable) dismissed()
    function activateIfPinned() {
        if (focusable && shown && visible) requestActivate();
    }
    onFocusableChanged: Qt.callLater(activateIfPinned)
    onShownChanged: Qt.callLater(activateIfPinned)
    onVisibleChanged: Qt.callLater(activateIfPinned)

    PopupFrame {
        id: frame
        shown: win.shown && !!win.content.component
        itemId: win.itemId
        content: win.content
        y: win.spansScreen ? win.anchorY : win.pad - 4
        x: win.spansScreen
            ? Math.max(8, Math.min(win.anchorX - width / 2, win.width - width - 8))
            : win.pad
        width: implicitWidth
        height: implicitHeight
        roomLeft: x
        roomRight: win.width - x - width
        onSideRectChanged: win.updateMask()
        onXChanged: win.updateMask()
        onWidthChanged: win.updateMask()
        onHeightChanged: win.updateMask()
        onYChanged: win.updateMask()
    }
    function updateMask() {
        if (!spansScreen) return;
        const rects = [Qt.rect(frame.x, frame.y, frame.width, frame.height)];
        const side = frame.sideRect;
        if (side) rects.push(Qt.rect(frame.x + side.x, frame.y + side.y, side.width, side.height));
        bar.setInputRects(win, rects);
    }
    Shortcut { sequence: "Escape"; onActivated: win.dismissed() }
}
