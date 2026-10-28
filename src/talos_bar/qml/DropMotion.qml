import QtQuick

// Reveal a card vertically from its parent's top edge. Normalized progress
// keeps the origin correct even when asynchronous content changes its height.
Translate {
    id: motion
    property bool shown: false
    property real distance: 0
    property real progress: 0
    readonly property bool animating: animation.running
    y: (progress - 1) * distance

    onShownChanged: {
        animation.stop();
        animation.from = shown ? 0 : progress;
        animation.to = shown ? 1 : 0;
        animation.start();
    }
    property NumberAnimation animation: NumberAnimation {
        target: motion
        property: "progress"
        duration: 200
        easing.type: Easing.OutCubic
    }
}
