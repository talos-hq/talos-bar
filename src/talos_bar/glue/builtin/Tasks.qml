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
    property bool applyingContent: false
    property bool saving: false
    property int submittedRevision: -1
    property string baseText: ""
    property bool saveError: false
    property string saveErrorMessage: ""
    property int revision: 0

    function save() {
        if (!dirty || (saving && submittedRevision === revision)) return;
        saving = true;
        submittedRevision = revision;
        bar.action("tasks", "save", JSON.stringify({text: editor.text, base: baseText, revision: root.revision}));
    }
    onContentChanged: {
        if (content.text === undefined || dirty) return;
        applyingContent = true;
        if (editor.text !== content.text) editor.text = content.text;
        applyingContent = false;
        baseText = content.baseText !== undefined ? content.baseText : content.text;
        dirty = !!content.editorDirty;
        revision = content.editorRevision || 0;
        saving = !!content.saving;
        saveError = !!content.saveError;
        saveErrorMessage = content.saveErrorMessage || "";
        loaded = true;
    }
    Component.onDestruction: save()
    Connections {
        target: bar
        function onPopupChanged() { if (bar.openId !== "tasks") root.save(); }
        function onActionFinished(itemId, name, payload, success, message) {
            if (itemId !== "tasks" || name !== "save") return;
            const edit = JSON.parse(payload);
            const saved = edit.text;
            root.saving = bar.hasPendingActions("tasks");
            root.saveError = !success;
            root.saveErrorMessage = message;
            if (success) {
                root.baseText = saved;
                if (!root.saving && editor.text === saved && edit.revision === root.revision) root.dirty = false;
            }
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
                text: !root.loaded ? "loading…" : root.saveError ? "Save failed or file changed; draft kept"
                    : root.saving ? "saving…" : root.dirty ? "editing…"
                    : root.content.synced ? "synced" : "saved, syncs at 0/6/12/18h"
                color: Theme.text
                opacity: 0.6
                HoverHandler { id: saveStatusHover }
                ToolTip.visible: saveStatusHover.hovered && root.saveError
                ToolTip.text: root.saveErrorMessage
            }
            Button {
                text: "Retry save"
                visible: root.saveError
                enabled: !root.saving
                onClicked: root.save()
            }
            Button {
                text: "Discard draft and reload"
                visible: root.saveError
                enabled: !root.saving
                onClicked: {
                    root.dirty = false;
                    bar.discardDraft("tasks");
                }
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
                objectName: "tasksEditor"
                color: Theme.text
                selectionColor: Qt.alpha(Theme.highlight, 0.5)
                font.family: "monospace"
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                selectByMouse: true
                TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onPressedChanged: {
                        if (pressed && bar.openId === "tasks" && !bar.popupFocus) bar.click("tasks");
                    }
                }
                readOnly: !root.loaded
                background: null
                onTextChanged: {
                    if (!root.loaded || root.applyingContent) return;
                    root.dirty = true;
                    root.revision = bar.setDraft("tasks", text, root.baseText);
                    saveTimer.restart();
                }
            }
        }
    }
}
