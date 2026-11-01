import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

// Super+Space search: a frameless, centered box. Typing queries talos-searchd; Enter
// activates the selected hit; Escape or losing focus hides it. Holding the mic button (or
// Ctrl+M, press again to stop) dictates through talos-vox into the field.
Window {
    id: win
    width: 640
    // A layer surface keeps the size it was shown with, so it stays at the maximum and the
    // drawn box shrinks inside it; a normal window simply tracks the box.
    readonly property int boxHeight: Math.min(96 + list.count * 48, 520)
    height: positioned ? boxHeight : 520
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    color: "transparent"
    visible: false
    // Wayland may refuse focus to a window the user did not click; only a popup that actually
    // gained focus hides when it loses it, so a refused activation still leaves it on screen.
    property bool hadFocus: false
    // Layer-shell surfaces are placed by the compositor, not by x/y.
    property bool positioned: true

    function open() {
        field.text = ""
        search.query("")
        if (positioned) {
            x = Screen.virtualX + (Screen.width - width) / 2
            y = Screen.virtualY + Screen.height / 4
        }
        hadFocus = false
        visible = true
        raise()
        requestActivate()
        field.forceActiveFocus()
    }

    function close() {
        search.dictate(false)
        visible = false
    }

    function activate(index) {
        if (index < 0 || index >= list.count) return
        search.activate(search.hits[index].id)
        close()
    }

    Connections {
        target: search
        function onShow() { win.visible ? win.close() : win.open() }
        function onDictated(text) {
            if (!win.visible) return
            field.text = text
            field.cursorPosition = text.length
        }
    }
    onActiveChanged: active ? hadFocus = true : (hadFocus && close())

    Rectangle {
        width: parent.width
        height: win.boxHeight
        radius: 12
        color: Theme.window
        border.color: Theme.highlight
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            RowLayout {
                spacing: 8
                TextField {
                    id: field
                    Layout.fillWidth: true
                    font.pixelSize: 20
                    placeholderText: !search.online ? "talos-searchd is not running"
                        : search.listening ? "Listening…" : "Search apps, go/ links…"
                    onTextChanged: { search.query(text); list.currentIndex = 0 }
                    Keys.onEscapePressed: win.close()
                    Keys.onDownPressed: list.incrementCurrentIndex()
                    Keys.onUpPressed: list.decrementCurrentIndex()
                    onAccepted: win.activate(list.currentIndex)
                    Shortcut {
                        sequence: "Ctrl+M"
                        onActivated: search.dictate(!search.listening)
                    }
                }
                ToolButton {
                    id: mic
                    icon.name: "audio-input-microphone"
                    checkable: false
                    highlighted: search.listening
                    enabled: search.online
                    ToolTip.visible: hovered
                    ToolTip.text: "Hold to talk (Ctrl+M)"
                    onPressed: search.dictate(true)
                    onReleased: search.dictate(false)
                    onCanceled: search.dictate(false)
                }
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: search.hits
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 48
                    highlighted: ListView.isCurrentItem
                    onClicked: win.activate(index)
                    contentItem: RowLayout {
                        spacing: 10
                        Image {
                            source: modelData.icon ? "image://icon/" + modelData.icon : ""
                            sourceSize: Qt.size(28, 28)
                            Layout.preferredWidth: 28
                            Layout.preferredHeight: 28
                        }
                        ColumnLayout {
                            spacing: 0
                            Label { text: modelData.title; font.pixelSize: 15 }
                            Label { text: modelData.subtitle; font.pixelSize: 11; opacity: 0.6 }
                        }
                    }
                }
            }
        }
    }
}
