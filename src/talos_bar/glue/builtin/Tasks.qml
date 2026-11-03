import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../qml"

// TASKS.md as a plain text editor: a port of the org.niteris.tasks plasmoid.
// Typing commits locally 1.5 s after it stops and when the popup closes; the
// tasks-sync timer pushes four times a day.
Item {
    id: root
    // Fixed like the plasmoid's popup; the editor scrolls inside it.
    implicitWidth: 560
    implicitHeight: 620
    readonly property var content: parent ? parent.content : ({})
    // True while the editor holds keystrokes not yet handed to the backend;
    // a refresh of `content` must not overwrite them.
    property bool dirty: false
    property bool loaded: false
    property bool saving: false
    property string submitted: ""

    function save() {
        if (!dirty || (saving && submitted === editor.text)) return;
        saving = true;
        submitted = editor.text;
        bar.action("tasks", "save", editor.text);
    }
    onContentChanged: {
        if (content.text === undefined || dirty) return;
        if (editor.text !== content.text) editor.text = content.text;
        loaded = true;
    }
    Component.onDestruction: save()
    Connections {
        target: bar
        function onPopupChanged() { if (bar.openId !== "tasks") root.save(); }
        function onActionFinished(itemId, name, payload, success) {
            if (itemId !== "tasks" || name !== "save") return;
            if (payload === root.submitted) root.saving = false;
            if (success && editor.text === payload) root.dirty = false;
        }
    }
    Timer { id: saveTimer; interval: 1500; onTriggered: root.save() }
    // Type straight away only when the user clicked the tab open.
    readonly property bool focusable: bar.popupFocus
    onFocusableChanged: if (focusable) editor.forceActiveFocus()
    Component.onCompleted: if (focusable) editor.forceActiveFocus()

    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Label { text: "TASKS.md"; color: Theme.text; font.bold: true; font.pixelSize: 16 }
            Label {
                Layout.fillWidth: true
                text: !root.loaded ? "loading…" : root.dirty ? "editing…"
                    : root.content.synced ? "synced" : "saved, syncs at 0/6/12/18h"
                color: Theme.text
                opacity: 0.6
            }
            ToolButton {
                icon.source: "image://icon/internet-services"
                icon.color: "transparent"
                ToolTip.visible: hovered
                ToolTip.text: "Open on GitHub"
                onClicked: { Qt.openUrlExternally("https://github.com/zackees/tasks/blob/main/TASKS.md"); bar.close(); }
            }
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            background: Rectangle { radius: 5; color: Qt.rgba(0, 0, 0, 0.25); border.width: 1; border.color: Qt.alpha(Theme.text, 0.1) }
            TextArea {
                id: editor
                color: Theme.text
                selectionColor: Qt.alpha(Theme.highlight, 0.5)
                font.family: "monospace"
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                selectByMouse: true
                background: null
                onTextChanged: {
                    if (!root.loaded || text === root.content.text) return;
                    root.dirty = true;
                    bar.setDraft("tasks", text);
                    saveTimer.restart();
                }
            }
        }
    }
}
