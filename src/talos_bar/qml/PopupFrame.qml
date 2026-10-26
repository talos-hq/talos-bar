import QtQuick
import QtQuick.Effects

// The popup's body: bar-style translucent chrome with a shadow, the glue's
// component inside, and the open/close animation (fade + slide down).
Item {
    id: frame
    property bool shown: false
    property string itemId: ""
    property var content: ({})
    readonly property bool hovered: hover.hovered
    readonly property bool animating: anim.running
    implicitWidth: loader.implicitWidth + 24
    implicitHeight: loader.implicitHeight + 24

    opacity: shown ? 1 : 0
    transform: Translate { y: frame.shown ? 0 : -8; Behavior on y { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } } }
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
    }
    HoverHandler { id: hover }
}
