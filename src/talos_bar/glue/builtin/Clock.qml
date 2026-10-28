import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../qml"

// A compact, read-only timezone table. All values come from one instant so
// the local clock, remote seconds and day labels cannot disagree at midnight.
ColumnLayout {
    id: clock
    readonly property var content: parent ? parent.content : ({})
    readonly property var zones: content.rows || []
    readonly property real cityWidth: zones.reduce((width, row) =>
        Math.max(width, metrics.advanceWidth(row.city + ":")), 0)
    spacing: 2

    FontMetrics { id: metrics; font: dateLabel.font }
    Label {
        id: dateLabel
        text: clock.content.date || ""
        textFormat: Text.PlainText
        color: Theme.text
    }
    Label {
        text: clock.content.local ? clock.content.local.city + ": " + clock.content.local.time : ""
        textFormat: Text.PlainText
        color: Theme.text
        font.bold: true
        font.features: ({"tnum": 1})
    }
    ColumnLayout {
        spacing: 0
        Repeater {
            model: clock.zones
            delegate: RowLayout {
                required property var modelData
                spacing: 4
                Label {
                    text: parent.modelData.city + ":"
                    textFormat: Text.PlainText
                    color: Theme.text
                    horizontalAlignment: Text.AlignRight
                    Layout.minimumWidth: clock.cityWidth
                    Layout.preferredWidth: clock.cityWidth
                }
                Label {
                    text: parent.modelData.time
                        + (parent.modelData.day ? " " + parent.modelData.day : "")
                        + " • " + parent.modelData.difference
                    textFormat: Text.PlainText
                    color: Theme.text
                    font.features: ({"tnum": 1})
                }
            }
        }
    }
}
