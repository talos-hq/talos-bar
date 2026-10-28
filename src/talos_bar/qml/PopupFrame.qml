import QtQuick
import QtQuick.Effects

// The popup's body: bar-style translucent chrome with a shadow, the glue's
// component inside, and the open/close animation (fade + slide down).
Item {
    id: frame
    property bool shown: false
    property string itemId: ""
    property var content: ({})
    // Room beside the frame inside the popup surface, for side panels.
    property real roomLeft: 0
    property real roomRight: 0
    // A component may draw a side panel outside its own bounds (the agenda's
    // meeting details); it reports it as `sideRect` and `sideHovered`.
    readonly property var sideRect: loader.item && loader.item.sideRect
        ? Qt.rect(loader.x + loader.item.sideRect.x, loader.y + loader.item.sideRect.y,
                  loader.item.sideRect.width, loader.item.sideRect.height)
        : null
    readonly property bool hovered: hover.hovered || !!(loader.item && loader.item.sideHovered)
    readonly property bool animating: anim.running || motion.animating
    readonly property real slideOffset: motion.y
    implicitWidth: loader.implicitWidth + 24
    implicitHeight: loader.implicitHeight + 24

    opacity: shown ? 1 : 0
    transform: DropMotion { id: motion; shown: frame.shown; distance: frame.height }
    Behavior on opacity { NumberAnimation { id: anim; duration: 140; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.window
        border.width: 1
        border.color: Qt.alpha(Theme.text, 0.08)
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: 0.5
            shadowBlur: 0.8
            shadowVerticalOffset: 3
        }
    }
    Loader {
        id: loader
        x: 12
        y: 12
        source: frame.content.component || ""
        // Components read `content` and `itemId`, and call bar.action().
        property var content: frame.content
        property string itemId: frame.itemId
        property real roomLeft: frame.roomLeft + 12
        property real roomRight: frame.roomRight + 12
    }
    HoverHandler { id: hover }
}
