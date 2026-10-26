import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

// One widget on the bar. Highlighted pill (like the agenda and tasks
// plasmoids) unless `plain`, which is bare text like the digital clock.
Item {
    id: tab
    required property var model
    property bool expanded: false
    readonly property bool hovered: hover.hovered
    signal activated()

    implicitWidth: (model.plain ? content.implicitWidth + 12 : Math.max(64, content.implicitWidth + 22))
    implicitHeight: Theme.barHeight

    Rectangle {
        visible: !tab.model.plain
        anchors.fill: parent
        anchors.margins: 4
        radius: 7
        color: Qt.alpha(Theme.highlight, tab.hovered || tab.expanded ? 0.23 : 0.10)
        border.width: 1
        border.color: Qt.alpha(Theme.highlight, 0.35)
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    Rectangle {
        // The plain style still answers the pointer, as Plasma's clock does.
        visible: tab.model.plain && (tab.hovered || tab.expanded)
        anchors.fill: parent
        anchors.margins: 3
        radius: 6
        color: Qt.alpha(Theme.text, 0.08)
    }

    // Plasma's expanded-applet marker: a highlight bar along the tab's foot.
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        height: 3
        radius: 1.5
        width: tab.expanded ? parent.width - 8 : 0
        color: Theme.highlight
        Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    }

    RowLayout {
        id: content
        anchors.centerIn: parent
        spacing: 5
        Image {
            visible: tab.model.icon !== ""
            source: tab.model.icon !== "" ? "image://icon/" + tab.model.icon : ""
            sourceSize: Qt.size(16, 16)
            Layout.preferredWidth: 16
            Layout.preferredHeight: 16
        }
        Rectangle {
            visible: !!tab.model.dot
            Layout.preferredWidth: 7
            Layout.preferredHeight: 7
            radius: 3.5
            color: tab.model.dot || "transparent"
        }
        ColumnLayout {
            spacing: -2
            Label {
                Layout.alignment: Qt.AlignHCenter
                text: tab.model.text
                color: tab.model.error ? Theme.negative : Theme.text
                font.pixelSize: tab.model.subtext !== "" ? 15 : 12
                font.bold: !tab.model.plain
            }
            Label {
                visible: tab.model.subtext !== ""
                Layout.alignment: Qt.AlignHCenter
                text: tab.model.subtext
                color: Theme.text
                font.pixelSize: 11
            }
        }
    }

    HoverHandler { id: hover; cursorShape: tab.model.expandable ? Qt.PointingHandCursor : Qt.ArrowCursor }
    TapHandler { onTapped: tab.activated() }
}
