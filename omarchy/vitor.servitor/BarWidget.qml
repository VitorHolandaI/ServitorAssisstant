import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar switch for the always-on wake-word listener.
//
// The daemon is the source of truth, not this widget: it streams its state on
// stdout and the widget only renders what arrives. That way the indicator can
// never claim the microphone is off while it is in fact open, which is the one
// lie a control like this must never tell.
BarWidget {
  id: root
  moduleName: "vitor.servitor"

  readonly property string earBin: Quickshell.env("SERVITOR_EAR_BIN")
    || (Quickshell.env("HOME") + "/tinker_git/ServitorAssisstant/scripts/servitor-ear")

  property string state: "off"
  property bool enabled: false
  property string wakePhrase: ""
  property string detail: ""

  readonly property var glyphs: ({
    "off": "○",
    "listening": "◉",
    "awake": "●",
    "recording": "●",
    "thinking": "◔",
    "speaking": "▶",
    "error": "!"
  })
  readonly property string glyph: glyphs[state] !== undefined ? glyphs[state] : "○"

  readonly property color tint: state === "error" ? "#e06c75"
    : (state === "off" ? (bar ? bar.barForeground : "white") : Color.accent)
  readonly property real tintOpacity: state === "off" ? 0.45 : 1.0

  implicitWidth: root.vertical ? root.barSize : 26
  implicitHeight: root.barSize

  // Long-lived: the daemon holds the connection open and pushes every change.
  Process {
    id: watcher
    command: [root.earBin, "stream"]
    running: true
    stdout: SplitParser {
      onRead: function (line) {
        try {
          var payload = JSON.parse(line)
          root.state = payload.state !== undefined ? payload.state : "off"
          root.enabled = payload.enabled === true
          root.wakePhrase = payload.wake_phrase !== undefined && payload.wake_phrase !== null ? payload.wake_phrase : ""
          root.detail = payload.detail !== undefined && payload.detail !== null ? payload.detail : ""
        } catch (error) {
          console.warn("vitor.servitor: bad status line", error)
        }
      }
    }
    // `stream` exits when the daemon dies. Reconnect so the widget recovers
    // on its own once the service comes back.
    onExited: reconnect.start()
  }

  Timer {
    id: reconnect
    interval: 2000
    repeat: false
    onTriggered: { root.state = "off"; root.enabled = false; watcher.running = true }
  }

  Process { id: toggler; command: [root.earBin, "toggle"] }

  function tooltipText() {
    if (detail !== "") return "Servitor ear: " + state + " (" + detail + ")"
    if (state === "off") return "Servitor ear: off - click to listen"
    return "Servitor ear: " + state + " - says \"" + wakePhrase + "\""
  }

  Item {
    id: button
    anchors.fill: parent

    Text {
      anchors.centerIn: parent
      text: root.glyph
      color: root.tint
      opacity: root.tintOpacity
      font.family: root.bar ? root.bar.fontFamily : "monospace"
      font.pixelSize: 13

      // A quiet pulse while the microphone is actually open for a command,
      // so an accidental wake is visible from across the room.
      SequentialAnimation on opacity {
        running: root.state === "recording"
        loops: Animation.Infinite
        NumberAnimation { from: 1.0; to: 0.35; duration: 600 }
        NumberAnimation { from: 0.35; to: 1.0; duration: 600 }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton
      onClicked: toggler.running = true
      onEntered: if (root.bar) root.bar.showTooltip(root, root.tooltipText())
      onExited: if (root.bar) root.bar.hideTooltip(root)
    }
  }
}
