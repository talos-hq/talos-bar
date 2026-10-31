import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

// The bar's right-click menu: which screen it lives on. The choice is saved
// and survives restarts and monitors sleeping (see platform.Placement).
ColumnLayout {
    id: menu
    spacing: 1
    implicitWidth: Math.max(260, rows.implicitWidth)

    Label {
        text: "Show bar on"
        color: Theme.text
        font.bold: true
        Layout.bottomMargin: 4
    }
    ColumnLayout {
        id: rows
        spacing: 1
        Layout.fillWidth: true
        Repeater {
            model: placement ? placement.screens : []
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitWidth: line.implicitWidth + 16
                implicitHeight: line.implicitHeight + 6
                radius: 5
                color: hover.hovered ? Qt.alpha(Theme.highlight, 0.18) : "transparent"
                Label {
                    id: line
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: (modelData.current ? "✓  " : "    ") + modelData.label
                    color: Theme.text
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        menu.Window.window.dismissed();
                        placement.moveTo(modelData.name);
                    }
                }
            }
        }
    }
}
