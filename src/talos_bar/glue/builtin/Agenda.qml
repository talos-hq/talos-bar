import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../qml"

// Today's meetings: a port of the org.niteris.calendar-agenda plasmoid. The
// list stays under its tab; a hovered (or clicked) meeting's full details
// drop down beside it as a side panel, drawn outside this item's bounds on
// whichever side has room (PopupFrame.roomLeft/roomRight), as SidePopup.qml
// does in the plasmoid.
Item {
    id: root
    implicitWidth: list.implicitWidth
    implicitHeight: list.implicitHeight
    readonly property var agenda: parent ? parent.content : ({})
    readonly property var events: agenda.events || []
    readonly property var done: agenda.done || []
    readonly property var tomorrow: agenda.tomorrow || []
    readonly property bool empty: !agenda.next && events.length === 0 && done.length === 0 && tomorrow.length === 0
    readonly property string title: agenda.next ? agenda.next.title : (agenda.countdown || "")

    property bool doneOpen: false
    readonly property bool showDone: doneOpen || events.length === 0
    property var focusRow: null
    property var pinnedRow: null
    readonly property var shownRow: pinnedRow || focusRow
    readonly property real roomLeft: parent ? parent.roomLeft : 0
    readonly property real roomRight: parent ? parent.roomRight : 0
    readonly property int sideWidth: 380
    // Read by PopupFrame: the side panel's area (for input) and hover.
    readonly property var sideRect: details.visible ? Qt.rect(details.x, details.y + detailsMotion.y, details.width, details.height) : null
    readonly property bool sideHovered: detailsHover.hovered || copyMenu.opened

    readonly property color link: Qt.rgba(41 / 255, 128 / 255, 185 / 255, 1).lighter(1.35)
    // Tomorrow's rows: semi-transparent yellow, so they lead without shouting.
    readonly property color tomorrowColor: Qt.alpha("#f5c518", 0.7)
    // The big Join button waits until a meeting is in progress or this close
    // to starting, so it never suggests an hour-off meeting is ready to join.
    readonly property int joinLeadSeconds: 5 * 60
    property real now: Date.now() / 1000
    Timer { interval: 15000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }
    readonly property bool joinable: !!agenda.next && agenda.next.link_kind === "Zoom"
        && (agenda.live === true || agenda.next.start - now <= joinLeadSeconds)

    function openMeeting(row) {
        const url = row.join_url || row.url;
        if (!url) return;
        Qt.openUrlExternally(url);
        bar.close();
    }
    function hm(epoch) { return Qt.formatDateTime(new Date(epoch * 1000), "h:mm AP"); }
    function escapeHtml(text) {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
    function anchor(href, label) { return '<a href="' + escapeHtml(href) + '">' + escapeHtml(label) + '</a>'; }
    function fullDetails(row) {
        const parts = ["<b>" + escapeHtml(row.title) + "</b>", escapeHtml(row.time)];
        if (row.location !== "" && row.link_kind !== "Zoom") parts.push(escapeHtml(row.location));
        if (row.link_kind === "Zoom") parts.push(anchor(row.join_url || row.url, "Join Zoom meeting"));
        const contacts = row.emails.map(a => anchor("mailto:" + a, a))
            .concat(row.phones.map(n => anchor("tel:" + n.replace(/[^\d+]/g, ""), n)));
        if (contacts.length > 0) parts.push(contacts.join("<br>"));
        if (row.details !== "") parts.push(escapeHtml(row.details).replace(/\n/g, "<br>"));
        return parts.join("<br><br>");
    }

    component Text_: Label {
        color: Theme.text
        textFormat: Text.PlainText
    }

    // ── The list ──
    ColumnLayout {
        id: list
        width: root.empty ? 360 : 440
        spacing: 10

        // Header, shown even with no meetings so New Zoom Meeting is always there.
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Image { source: "image://icon/view-calendar-agenda"; sourceSize: Qt.size(26, 26) }
            ColumnLayout {
                spacing: 0
                Text_ { text: "Today's meetings"; font.bold: true; font.pixelSize: 18 }
                Text_ { text: root.agenda.date || "Calendar"; opacity: 0.7 }
            }
            Item { Layout.fillWidth: true }
            // Start a new Zoom meeting hosted by the signed-in user; the web
            // page hands off to the Zoom app.
            Button {
                text: "New Zoom Meeting"
                icon.source: "image://icon/camera-video"
                icon.color: "transparent"
                onClicked: { Qt.openUrlExternally("https://zoom.us/start/videomeeting"); bar.close(); }
            }
            ToolButton {
                icon.source: "image://icon/view-calendar-day"
                icon.color: "transparent"
                ToolTip.visible: hovered
                ToolTip.text: "Open calendar"
                onClicked: { bar.action("agenda", "open_calendar", ""); bar.close(); }
            }
        }

        // Headliner: the meeting the tab shows, with one big Join button.
        Rectangle {
            visible: !!root.agenda.next
            Layout.fillWidth: true
            implicitHeight: headliner.implicitHeight + 24
            radius: 8
            color: Qt.alpha(Theme.highlight, 0.16)
            border.width: 1
            border.color: Qt.alpha(Theme.highlight, 0.45)
            HoverHandler { onHoveredChanged: if (hovered) root.focusRow = root.agenda.next }
            ColumnLayout {
                id: headliner
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6
                RowLayout {
                    spacing: 6
                    Rectangle {
                        visible: root.agenda.live === true
                        Layout.preferredWidth: 8
                        Layout.preferredHeight: 8
                        radius: 4
                        color: Theme.negative
                    }
                    Text_ {
                        text: root.agenda.live ? "In progress" : (root.agenda.next_label || "") + " · " + (root.agenda.countdown || "")
                        font.bold: true
                        color: Theme.highlight
                    }
                }
                Text_ {
                    Layout.fillWidth: true
                    text: root.agenda.next && !root.agenda.live ? "Next up: " + root.title : root.title
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    font.pixelSize: 20
                    font.bold: true
                }
                Text_ { text: root.agenda.next ? root.agenda.next.time : ""; opacity: 0.75 }
                Button {
                    id: join
                    visible: root.joinable
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    onClicked: root.openMeeting(root.agenda.next)
                    background: Rectangle {
                        radius: 5
                        color: join.hovered ? Qt.alpha(Theme.highlight, 0.25) : Qt.rgba(0, 0, 0, 0.25)
                        border.width: 1
                        border.color: Qt.alpha(Theme.text, 0.15)
                    }
                    contentItem: RowLayout {
                        spacing: 6
                        Item { Layout.fillWidth: true }
                        Image { source: "image://icon/camera-video"; sourceSize: Qt.size(18, 18) }
                        Text_ {
                            text: "Join Zoom meeting now"
                            font.pixelSize: 15
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }

        Text_ {
            visible: root.events.length === 0 && root.done.length === 0
            Layout.fillWidth: true
            text: root.agenda.error || "No meetings today"
            wrapMode: Text.WordWrap
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }

        ScrollView {
            visible: root.events.length > 0 || root.done.length > 0 || root.tomorrow.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(420, eventList.implicitHeight)
            contentWidth: availableWidth
            clip: true
            Column {
                id: eventList
                width: parent.width
                spacing: 6
                // Once today is over, tomorrow leads, in translucent yellow;
                // the day just gone stays listed below it.
                Text_ {
                    visible: root.tomorrow.length > 0
                    width: eventList.width
                    text: "Tomorrow"
                    font.bold: true
                    color: root.tomorrowColor
                }
                Repeater { model: root.tomorrow; delegate: meetingRow }
                Text_ {
                    visible: root.tomorrow.length > 0 && (root.events.length > 0 || root.done.length > 0)
                    width: eventList.width
                    topPadding: 8
                    text: "Today"
                    font.bold: true
                    opacity: 0.8
                }
                Repeater { model: root.events; delegate: meetingRow }
                ItemDelegate {
                    visible: root.done.length > 0
                    width: eventList.width
                    enabled: root.events.length > 0
                    onClicked: root.doneOpen = !root.doneOpen
                    contentItem: RowLayout {
                        Image {
                            source: "image://icon/" + (root.showDone ? "arrow-down" : "arrow-right")
                            sourceSize: Qt.size(16, 16)
                        }
                        Text_ { text: "Done · " + root.done.length; font.bold: true; opacity: 0.8 }
                    }
                }
                Repeater { model: root.showDone ? root.done : []; delegate: meetingRow }
            }
        }
    }

    // ── Full details of the hovered or pinned meeting, beside the list ──
    Rectangle {
        id: details
        // Right of the list when it fits, else left; the list never moves.
        readonly property bool onRight: root.roomRight >= root.sideWidth + 24 || root.roomLeft < root.sideWidth + 24
        readonly property bool shown: !root.empty && root.shownRow !== null
        visible: shown || detailsMotion.animating
        transform: DropMotion { id: detailsMotion; shown: details.shown; distance: details.height }
        width: root.sideWidth
        height: Math.max(root.height, 200)
        x: onRight ? root.width + 24 : -root.sideWidth - 24
        y: -12
        radius: 8
        color: Theme.window
        border.width: 1
        border.color: Qt.alpha(Theme.highlight, 0.35)
        opacity: shown ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 120 } }
        HoverHandler { id: detailsHover }
        ScrollView {
            anchors.fill: parent
            anchors.margins: 12
            contentWidth: availableWidth
            clip: true
            TextArea {
                id: detailText
                objectName: "agendaDetails"
                readOnly: true
                selectByMouse: true
                persistentSelection: true
                background: null
                padding: 0
                width: parent ? parent.width : 0
                text: root.shownRow ? root.fullDetails(root.shownRow) : ""
                textFormat: TextEdit.RichText
                wrapMode: Text.Wrap
                color: Theme.text
                palette.link: root.link
                onLinkActivated: function(href) {
                    Qt.openUrlExternally(href);
                    bar.close();
                }
                TapHandler {
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onPressedChanged: {
                        if (!pressed) return;
                        root.pinnedRow = root.shownRow;
                        if (bar.openId === "agenda" && !bar.popupFocus) bar.click("agenda");
                        detailText.forceActiveFocus();
                    }
                }
                // Handle only right clicks so dragging and left-click links keep
                // the text control's normal selection and activation behavior.
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.RightButton
                    onClicked: function(mouse) {
                        root.pinnedRow = root.shownRow;
                        if (bar.openId === "agenda" && !bar.popupFocus) bar.click("agenda");
                        detailText.forceActiveFocus();
                        copyMenu.link = detailText.linkAt(mouse.x, mouse.y);
                        const point = detailText.mapToItem(details, mouse.x, mouse.y);
                        copyMenu.popup(Math.max(0, Math.min(point.x, details.width - copyMenu.implicitWidth)),
                                       Math.max(0, Math.min(point.y, details.height - copyMenu.implicitHeight)));
                    }
                }
                Menu {
                    id: copyMenu
                    parent: details
                    objectName: "agendaCopyMenu"
                    property string link: ""
                    MenuItem {
                        objectName: "agendaCopyLink"
                        text: copyMenu.link.startsWith("mailto:") ? "Copy email link" : "Copy link address"
                        visible: copyMenu.link !== ""
                        onTriggered: {
                            // A separate invisible text control copies the URI
                            // without disturbing the user's selected detail text.
                            linkCopy.text = copyMenu.link;
                            linkCopy.selectAll();
                            linkCopy.copy();
                        }
                    }
                    MenuItem {
                        objectName: "agendaCopyText"
                        text: "Copy"
                        enabled: detailText.selectedText !== ""
                        onTriggered: detailText.copy()
                    }
                    MenuItem {
                        text: "Select all"
                        onTriggered: detailText.selectAll()
                    }
                }
                TextEdit { id: linkCopy; visible: false }
                HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
            }
        }
    }

    Component {
        id: meetingRow
        ItemDelegate {
            id: meeting
            required property var modelData
            readonly property color accent: modelData.tomorrow ? root.tomorrowColor
                : modelData.status === "current" ? Theme.highlight : Theme.text
            width: parent ? parent.width : 0
            implicitHeight: details.implicitHeight + 18
            opacity: modelData.status === "past" ? 0.5 : 1.0
            onClicked: root.pinnedRow = modelData
            onHoveredChanged: if (hovered) root.focusRow = modelData
            background: Rectangle {
                color: meeting.modelData === root.shownRow || meeting.modelData.status === "current"
                    ? Qt.alpha(Theme.highlight, 0.18)
                    : meeting.hovered ? Qt.alpha(Theme.highlight, 0.10) : "transparent"
            }
            contentItem: ColumnLayout {
                id: details
                spacing: 4
                Text_ {
                    Layout.fillWidth: true
                    text: (meeting.modelData.status === "current" ? "NOW · " : "") + meeting.modelData.time
                    font.bold: true
                    color: meeting.accent
                }
                Text_ {
                    Layout.fillWidth: true
                    text: meeting.modelData.title
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    font.pixelSize: 14
                    color: meeting.modelData.tomorrow ? root.tomorrowColor : Theme.text
                }
                Text_ {
                    visible: meeting.modelData.link_kind !== "Zoom"
                        && (meeting.modelData.link_kind !== "" || meeting.modelData.location !== "")
                    Layout.fillWidth: true
                    text: meeting.modelData.location || "Open calendar event"
                    wrapMode: Text.WordWrap
                    opacity: 0.7
                }
                RowLayout {
                    visible: meeting.modelData.link_kind === "Zoom"
                    Layout.fillWidth: true
                    spacing: 4
                    Image { source: "image://icon/camera-video"; sourceSize: Qt.size(14, 14) }
                    Text_ {
                        Layout.fillWidth: true
                        // Drop the query string: it can carry the meeting passcode.
                        text: meeting.modelData.url.replace(/^https?:\/\//, "").split("?")[0]
                        elide: Text.ElideRight
                        color: root.link
                        font.underline: true
                        TapHandler { onTapped: root.openMeeting(meeting.modelData) }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
                Repeater {
                    model: meeting.modelData.emails.map(address => ({label: address, href: "mailto:" + address}))
                        .concat(meeting.modelData.phones.map(number => ({label: number, href: "tel:" + number.replace(/[^\d+]/g, "")})))
                    delegate: Text_ {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.label
                        elide: Text.ElideRight
                        color: root.link
                        font.underline: true
                        TapHandler { onTapped: { Qt.openUrlExternally(parent.modelData.href); bar.close(); } }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }
        }
    }
}
