import QtQuick
import QtQuick.Controls

Window {
    id: root
    visible: false
    color: "transparent"
    width: row.implicitWidth + 32
    height: 36

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: Qt.rgba(0.08, 0.09, 0.11, 0.7)
        border.color: Qt.rgba(1, 1, 1, 0.08)
    }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 20

        Repeater {
            model: bar.items
            delegate: Label {
                required property var modelData
                text: modelData.text
                color: modelData.error ? "#ff6b6b" : "#e8e8ea"
                font.pixelSize: 14
                ToolTip.visible: modelData.tooltip !== "" && hover.hovered
                ToolTip.text: modelData.tooltip
                HoverHandler { id: hover }
                TapHandler { onTapped: bar.click(modelData.id) }
            }
        }
    }
}
