import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

// Default popup body: {title, subtitle, rows: [{text, subtext, icon, url}]}.
ColumnLayout {
    id: list
    spacing: 4
    property var data_: parent ? parent.content : ({})
    implicitWidth: Math.max(220, rows.implicitWidth)

    Label {
        visible: !!list.data_.title
        text: list.data_.title || ""
        color: Theme.text
        font.bold: true
        font.pixelSize: 16
    }
    Label {
        visible: !!list.data_.subtitle
        text: list.data_.subtitle || ""
        color: Theme.text
        opacity: 0.7
    }
    ColumnLayout {
        id: rows
        spacing: 1
        Layout.fillWidth: true
        Repeater {
            model: list.data_.rows || []
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitWidth: line.implicitWidth + 16
                implicitHeight: line.implicitHeight + 2
                radius: 5
                color: rowHover.hovered ? Qt.alpha(Theme.highlight, 0.18) : "transparent"
                RowLayout {
                    id: line
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: 16
                    Image {
                        visible: !!modelData.icon
                        source: modelData.icon ? "image://icon/" + modelData.icon : ""
                        sourceSize: Qt.size(16, 16)
                    }
                    Label { text: modelData.text || ""; color: Theme.text; Layout.fillWidth: true }
                    Label { text: modelData.subtext || ""; color: Theme.text; opacity: 0.75; font.bold: true }
                }
                HoverHandler { id: rowHover; cursorShape: modelData.url ? Qt.PointingHandCursor : Qt.ArrowCursor }
                TapHandler { onTapped: if (modelData.url) { Qt.openUrlExternally(modelData.url); bar.close(); } }
            }
        }
    }
}
