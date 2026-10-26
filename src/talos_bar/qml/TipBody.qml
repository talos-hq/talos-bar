import QtQuick
import QtQuick.Controls

// A tab's tooltip, shown in a popup frame (see Bar.qml's tooltips). It is a
// pop-in, not just a label: the pointer can move into it, and a click opens
// the tab's full popup (the tasks editor) as clicking the tab would.
Item {
    id: tip
    readonly property var content: parent ? parent.content : ({})
    readonly property string itemId: parent ? parent.itemId : ""
    implicitWidth: Math.min(lines.implicitWidth, 480)
    implicitHeight: lines.implicitHeight

    // One label per line: Qt elides only the last line of a multi-line
    // label, so long entries would run past the frame.
    Column {
        id: lines
        Repeater {
            model: (tip.content.text || "").split("\n")
            delegate: Label {
                required property string modelData
                width: Math.min(implicitWidth, 480)
                text: modelData
                color: Theme.text
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }
        }
    }
    HoverHandler { cursorShape: tip.content.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor }
    TapHandler { onTapped: if (tip.content.clickable) bar.click(tip.itemId) }
}
