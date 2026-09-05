import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// The card that appears while the Servitor is being spoken to.
//
// The bar glyph says which state the ear is in, but it is 13 pixels wide and
// sits at the edge of the screen. Once the wake phrase lands there is a real
// conversation happening, and the thing worth showing is what the machine
// believed it heard - a wake word is only useful if you can see when it
// misheard you.
//
// Built the way Omarchy's own OSD is built (plugins/osd/Osd.qml): an overlay
// layer-shell surface with an empty input region, so it floats above
// everything and blocks nothing.
Item {
  id: root

  property string earState: "off"
  property string heard: ""
  property string reply: ""
  property string wakePhrase: ""

  // A turn is in progress from the wake phrase until the answer is spoken.
  // "listening" and "off" are the resting states and draw nothing.
  readonly property bool turnActive: earState === "awake" || earState === "recording"
    || earState === "thinking" || earState === "speaking"

  // The card outlives the turn by a moment, so the transcript can be read
  // after the Servitor stops talking rather than vanishing with the audio.
  property bool lingering: false
  readonly property bool opened: turnActive || lingering

  readonly property int pad: Style.space(16)
  readonly property int cardWidth: Style.space(420)

  readonly property string phase: earState === "awake" ? "Listening"
    : earState === "recording" ? "Listening"
    : earState === "thinking" ? "Thinking"
    : earState === "speaking" ? "Speaking"
    : "Heard"

  readonly property string glyph: earState === "thinking" ? "◔"
    : earState === "speaking" ? "▶" : "◉"

  onTurnActiveChanged: {
    if (turnActive) {
      lingering = false
      linger.stop()
    } else if (root.heard !== "") {
      // Only linger when there is something to read. A wake with no command
      // should not leave a card sitting on the screen.
      lingering = true
      linger.restart()
    }
  }

  Timer {
    id: linger
    interval: 4000
    repeat: false
    onTriggered: root.lingering = false
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "servitor-ear-osd"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    // Visual only: an empty input region keeps clicks falling through to
    // whatever the user is actually working in.
    mask: Region {}

    BorderSurface {
      id: card
      width: root.cardWidth
      height: card.borderTop + root.pad + content.implicitHeight + root.pad + card.borderBottom
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: Style.space(67)
      color: Color.popups.background
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius

      opacity: root.opened ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: 140 } }
      // Rises into place rather than appearing, which reads as the assistant
      // arriving instead of the screen glitching.
      transform: Translate { y: root.opened ? 0 : Style.space(8)
        Behavior on y { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } } }

      Column {
        id: content
        anchors.fill: parent
        anchors.topMargin: card.borderTop + root.pad
        anchors.rightMargin: card.borderRight + root.pad
        anchors.bottomMargin: card.borderBottom + root.pad
        anchors.leftMargin: card.borderLeft + root.pad
        spacing: Style.space(8)

        Row {
          spacing: Style.space(10)

          Text {
            text: root.glyph
            color: Color.accent
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            anchors.verticalCenter: parent.verticalCenter

            // The same pulse the bar glyph uses while the microphone is open,
            // so the two indicators are visibly the same thing.
            SequentialAnimation on opacity {
              running: root.earState === "recording" || root.earState === "awake"
              loops: Animation.Infinite
              NumberAnimation { from: 1.0; to: 0.35; duration: 600 }
              NumberAnimation { from: 0.35; to: 1.0; duration: 600 }
            }
          }

          Text {
            text: root.phase
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            anchors.verticalCenter: parent.verticalCenter
          }
        }

        // Before anything is transcribed there is nothing to show but the
        // phrase that opened the turn, which also confirms the wake landed.
        Text {
          width: parent.width
          visible: root.heard === ""
          text: root.earState === "thinking" ? "…" : "say your command"
          color: Util.alpha(Color.popups.text, 0.45)
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          visible: root.heard !== ""
          text: root.heard
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
          maximumLineCount: 3
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          visible: root.reply !== ""
          text: root.reply
          color: Util.alpha(Color.popups.text, 0.6)
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
          maximumLineCount: 4
          elide: Text.ElideRight
        }
      }
    }
  }
}
