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

    Row {
        id: row
        x: Theme.shadowPad + 8
        height: Theme.barHeight
        spacing: 2

        Repeater {
            model: bar.items
            delegate: Tab {
                required property var modelData
                model: modelData
                onActivated: bar.click(modelData.id)
            }
        }
    }
}
