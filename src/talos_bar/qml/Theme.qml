pragma Singleton
import QtQuick

// Breeze Dark, as in ~/dev/nixos/home/kde/kdeglobals; the Plasma panel and
// its widgets draw with these.
QtObject {
    readonly property color window: Qt.rgba(32 / 255, 35 / 255, 38 / 255, 1)
    readonly property real panelOpacity: 0.7
    readonly property color text: Qt.rgba(252 / 255, 252 / 255, 252 / 255, 1)
    readonly property color highlight: Qt.rgba(61 / 255, 174 / 255, 233 / 255, 1)
    readonly property color negative: "#e5322d"
    readonly property color neutral: "#f67400"
    readonly property int barHeight: 36
    readonly property int shadowPad: 10
    readonly property int radius: 8
}
