import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../qml"

// Today's meetings: a port of the org.niteris.calendar-agenda plasmoid. The
// list is on the left, the hovered (or clicked) meeting's full details on the
// right; the details always show something, so the popup keeps one width.
RowLayout {
    id: root
    spacing: 12
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
    readonly property var shownRow: pinnedRow || focusRow || agenda.next
        || (events.length > 0 ? events[0] : null)
        || (done.length > 0 ? done[done.length - 1] : null)
        || (tomorrow.length > 0 ? tomorrow[0] : null)

    readonly property color link: Qt.rgba(41 / 255, 128 / 255, 185 / 255, 1).lighter(1.35)
    readonly property color positive: "#27ae60"

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
        if (row.link_kind === "Zoom") parts.push(anchor("zoom:", "Join Zoom meeting"));
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
        Layout.preferredWidth: root.empty ? 240 : 440
        Layout.alignment: Qt.AlignTop
        spacing: 10

        RowLayout {
            visible: !root.empty
            Layout.fillWidth: true
            spacing: 10
            Image { source: "image://icon/view-calendar-agenda"; sourceSize: Qt.size(26, 26) }
            ColumnLayout {
                spacing: 0
                Text_ { text: "Today's meetings"; font.bold: true; font.pixelSize: 18 }
                Text_ { text: root.agenda.date || "Calendar"; opacity: 0.7 }
            }
            Item { Layout.fillWidth: true }
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
                    visible: !!root.agenda.next && root.agenda.next.link_kind === "Zoom"
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
                            text: root.agenda.live ? "Join Zoom meeting now" : "Join Zoom meeting"
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
                Text_ {
                    visible: root.tomorrow.length > 0
                    width: eventList.width
                    topPadding: 8
                    text: "Tomorrow"
                    font.bold: true
                    color: root.positive
                }
                Repeater { model: root.tomorrow; delegate: meetingRow }
            }
        }
    }

    // ── Full details of the hovered or pinned meeting ──
    Rectangle {
        visible: !root.empty && root.shownRow !== null
        Layout.preferredWidth: 380
        Layout.fillHeight: true
        Layout.minimumHeight: 200
        radius: 8
        color: Qt.alpha(Theme.highlight, 0.08)
        border.width: 1
        border.color: Qt.alpha(Theme.highlight, 0.35)
        ScrollView {
            anchors.fill: parent
            anchors.margins: 12
            contentWidth: availableWidth
            clip: true
            Label {
                width: parent ? parent.width : 0
                text: root.shownRow ? root.fullDetails(root.shownRow) : ""
                textFormat: Text.StyledText
                wrapMode: Text.Wrap
                color: Theme.text
                linkColor: root.link
                onLinkActivated: function(href) {
                    if (href === "zoom:") root.openMeeting(root.shownRow);
                    else { Qt.openUrlExternally(href); bar.close(); }
                }
                HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
            }
        }
    }

    Component {
        id: meetingRow
        ItemDelegate {
            id: meeting
            required property var modelData
            readonly property color accent: modelData.tomorrow ? root.positive
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
                    color: meeting.modelData.tomorrow ? root.positive : Theme.text
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
