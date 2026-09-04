"""Tests for the wake-word listener.

Everything here runs without a microphone, an NPU or a model: the parts that
need hardware are behind seams, and what is tested is the logic that decides
whether the microphone is open, what the widget is told, and how audio is
framed on the way to Whisper.
"""
import json
import os
import sys
import threading
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ear import control  # noqa: E402
from ear.ear import (  # noqa: E402
    LISTENING,
    _EnergyGate,
    OFF,
    EarConfig,
    ServitorEar,
    _rms,
    _to_wav,
)
from ear.devices import connected_displays, guard_device  # noqa: E402
from ear.transcribe import wav_to_float32  # noqa: E402

EAR_ENV = {
    "EAR_WAKE_PHRASE": "",
    "EAR_VOSK_MODEL": "",
    "EAR_VOICE": "",
    "EAR_ACK_TEXT": "",
    "EAR_WHISPER_MODEL": "",
    "EAR_WHISPER_DEVICE": "",
    "EAR_LLM_MODEL": "",
    "EAR_LLM_DEVICE": "",
    "EAR_SPOOL_DIR": "",
    "EAR_START_ENABLED": "",
    "EAR_VAD": "",
    "EAR_ALLOW_SHARED_GPU": "",
}


def _env(**overrides):
    values = dict(EAR_ENV)
    values.update(overrides)
    return patch.dict(os.environ, values, clear=False)


class EarConfigTests(unittest.TestCase):
    def test_defaults_target_the_low_power_devices(self):
        with _env():
            config = EarConfig.from_env()
        # Whisper on the NPU is only safe because the pipeline is rebuilt per
        # utterance; if this default ever moves, that reasoning moves with it.
        self.assertEqual(config.whisper_device, "NPU")
        self.assertEqual(config.llm_device, "NPU")

    def test_wake_phrase_is_normalised(self):
        with _env(EAR_WAKE_PHRASE="  Hey Oracle  "):
            self.assertEqual(EarConfig.from_env().wake_phrase, "hey oracle")

    def test_device_is_upper_cased(self):
        with _env(EAR_LLM_DEVICE="gpu"):
            self.assertEqual(EarConfig.from_env().llm_device, "GPU")

    def test_paths_expand_the_home_shortcut(self):
        with _env(EAR_SPOOL_DIR="~/somewhere/spool"):
            self.assertEqual(EarConfig.from_env().spool_dir, Path.home() / "somewhere" / "spool")

    def test_listening_can_be_disabled_at_startup(self):
        with _env(EAR_START_ENABLED="false"):
            self.assertFalse(EarConfig.from_env().start_enabled)
        with _env(EAR_START_ENABLED="true"):
            self.assertTrue(EarConfig.from_env().start_enabled)


class DeviceGuardTests(unittest.TestCase):
    """The GPU also drives the desktop; a model hung it once already."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.drm = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _connector(self, name, status):
        card = self.drm / "card1" / name
        card.mkdir(parents=True)
        (card / "status").write_text(status + "\n", encoding="utf-8")

    def test_npu_and_cpu_pass_through_untouched(self):
        self._connector("card1-eDP-1", "connected")
        with _env():
            self.assertEqual(guard_device("NPU", "x", self.drm), "NPU")
            self.assertEqual(guard_device("CPU", "x", self.drm), "CPU")

    def test_gpu_is_refused_while_a_display_is_attached(self):
        self._connector("card1-eDP-1", "connected")
        with _env():
            self.assertEqual(guard_device("GPU", "x", self.drm), "NPU")

    def test_gpu_is_allowed_when_nothing_is_plugged_in(self):
        self._connector("card1-HDMI-A-1", "disconnected")
        with _env():
            self.assertEqual(guard_device("GPU", "x", self.drm), "GPU")

    def test_the_override_is_honoured(self):
        self._connector("card1-eDP-1", "connected")
        with _env(EAR_ALLOW_SHARED_GPU="true"):
            self.assertEqual(guard_device("GPU", "x", self.drm), "GPU")

    def test_lowercase_device_is_still_guarded(self):
        self._connector("card1-eDP-1", "connected")
        with _env():
            self.assertEqual(guard_device("gpu", "x", self.drm), "NPU")

    def test_missing_drm_tree_is_not_an_error(self):
        self.assertEqual(connected_displays(self.drm / "nope"), [])

    def test_only_connected_connectors_are_reported(self):
        self._connector("card1-eDP-1", "connected")
        self._connector("card1-DP-2", "disconnected")
        self.assertEqual(connected_displays(self.drm), ["card1-eDP-1"])


class EnergyGateTests(unittest.TestCase):
    """Stage zero. Wrong here means either a deaf ear or a wasted core."""

    def setUp(self):
        self.gate = _EnergyGate(preroll_blocks=3, hangover_blocks=4)

    def test_starts_closed_so_a_quiet_room_costs_nothing(self):
        self.assertEqual(self.gate.feed(b"q", loud=False), [])
        self.assertFalse(self.gate.open)

    def test_opens_on_sound(self):
        self.assertTrue(self.gate.feed(b"loud", loud=True))
        self.assertTrue(self.gate.open)

    def test_replays_the_preroll_so_the_first_syllable_survives(self):
        for name in (b"q1", b"q2", b"q3"):
            self.gate.feed(name, loud=False)
        out = self.gate.feed(b"speech", loud=True)
        self.assertEqual(out, [b"q1", b"q2", b"q3", b"speech"])

    def test_preroll_keeps_only_the_most_recent_blocks(self):
        for name in (b"q1", b"q2", b"q3", b"q4", b"q5"):
            self.gate.feed(name, loud=False)
        self.assertEqual(self.gate.feed(b"speech", loud=True), [b"q3", b"q4", b"q5", b"speech"])

    def test_stays_open_through_a_pause_between_words(self):
        self.gate.feed(b"a", loud=True)
        for _ in range(3):  # shorter than the 4-block hangover
            self.assertTrue(self.gate.feed(b"gap", loud=False))
        self.assertTrue(self.gate.open)

    def test_closes_after_the_hangover_expires(self):
        self.gate.feed(b"a", loud=True)
        for _ in range(4):
            self.gate.feed(b"gap", loud=False)
        self.assertFalse(self.gate.open)
        self.assertEqual(self.gate.feed(b"more", loud=False), [])

    def test_reports_the_exact_block_it_closed_on(self):
        """The decoder is reset once, on that edge — not on every quiet block."""
        self.gate.feed(b"a", loud=True)
        closings = [bool(self.gate.feed(b"q", loud=False) is not None and self.gate.just_closed)
                    for _ in range(8)]
        self.assertEqual(closings.count(True), 1)

    def test_reset_closes_it_and_drops_the_preroll(self):
        self.gate.feed(b"a", loud=True)
        self.gate.feed(b"q", loud=False)
        self.gate.reset()
        self.assertFalse(self.gate.open)
        self.assertEqual(self.gate.feed(b"q2", loud=False), [])


class DotenvTests(unittest.TestCase):
    def test_the_daemon_reads_the_projects_env_file(self):
        """Every EAR_* line in .env.example is a lie unless this holds."""
        source = (Path(__file__).resolve().parents[1] / "ear" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("load_dotenv", source)


class VadConfigTests(unittest.TestCase):
    def test_the_gate_is_on_by_default(self):
        with _env():
            self.assertTrue(EarConfig.from_env().vad_enabled)

    def test_the_gate_can_be_turned_off(self):
        with _env(EAR_VAD="false"):
            self.assertFalse(EarConfig.from_env().vad_enabled)

    def test_preroll_exists_so_the_first_syllable_is_not_clipped(self):
        with _env():
            self.assertGreater(EarConfig.from_env().vad_preroll_seconds, 0.0)


class AudioHelperTests(unittest.TestCase):
    def test_rms_separates_silence_from_speech(self):
        silence = np.zeros(1600, dtype=np.int16).tobytes()
        loud = (np.ones(1600) * 8000).astype(np.int16).tobytes()
        self.assertEqual(_rms(silence), 0.0)
        self.assertGreater(_rms(loud), 7000)

    def test_rms_of_nothing_is_zero(self):
        self.assertEqual(_rms(b""), 0.0)

    def test_recorded_audio_is_the_shape_vosk_and_whisper_need(self):
        pcm = (np.random.default_rng(0).integers(-3000, 3000, 16000)).astype(np.int16).tobytes()
        with wave.open(BytesIO(_to_wav(pcm)), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 16000)

    def test_wav_decodes_to_normalised_floats(self):
        pcm = np.array([0, 32767, -32768, 16384], dtype=np.int16).tobytes()
        decoded = wav_to_float32(_to_wav(pcm))
        self.assertEqual(decoded.dtype, np.float32)
        self.assertAlmostEqual(float(decoded[0]), 0.0)
        self.assertLessEqual(float(np.abs(decoded).max()), 1.0)

    def test_stereo_input_is_refused_rather_than_misread(self):
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00\x00\x00")
        with self.assertRaises(ValueError):
            wav_to_float32(buffer.getvalue())


class EarStateTests(unittest.TestCase):
    """The state the widget renders must match what the microphone is doing."""

    def setUp(self):
        self.config = EarConfig(start_enabled=False)
        self.ear = ServitorEar(self.config)

    def test_starts_disabled_when_configured_so(self):
        snapshot = self.ear.snapshot()
        self.assertFalse(snapshot["enabled"])
        self.assertEqual(snapshot["state"], OFF)

    def test_toggle_flips_and_reports_the_new_value(self):
        self.assertTrue(self.ear.toggle())
        self.assertTrue(self.ear.snapshot()["enabled"])
        self.assertFalse(self.ear.toggle())
        self.assertFalse(self.ear.snapshot()["enabled"])

    def test_subscriber_receives_the_current_state_immediately(self):
        seen = []
        self.ear.subscribe(seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["state"], OFF)

    def test_subscriber_receives_later_changes(self):
        seen = []
        self.ear.subscribe(seen.append)
        self.ear._set_state(LISTENING)
        self.assertEqual(seen[-1]["state"], LISTENING)

    def test_repeated_state_is_not_republished(self):
        seen = []
        self.ear.subscribe(seen.append)
        self.ear._set_state(LISTENING)
        self.ear._set_state(LISTENING)
        self.assertEqual([s["state"] for s in seen], [OFF, LISTENING])

    def test_a_broken_subscriber_cannot_stop_the_ear(self):
        def explode(_payload):
            raise RuntimeError("widget went away")

        self.ear.subscribe(explode)
        self.ear._set_state(LISTENING)  # must not raise
        self.assertEqual(self.ear.snapshot()["state"], LISTENING)

    def test_unsubscribe_stops_delivery(self):
        seen = []
        self.ear.subscribe(seen.append)
        self.ear.unsubscribe(seen.append)
        before = len(seen)
        self.ear._set_state(LISTENING)
        self.assertEqual(len(seen), before)


class _FakeEar:
    """Just enough ear for the control plane to talk to."""

    def __init__(self):
        self.enabled = False
        self.subscribers = []

    def snapshot(self):
        return {"state": LISTENING if self.enabled else OFF, "enabled": self.enabled}

    def toggle(self):
        self.enabled = not self.enabled
        return self.enabled

    def set_enabled(self, enabled):
        self.enabled = enabled

    def subscribe(self, callback):
        self.subscribers.append(callback)
        callback(self.snapshot())

    def unsubscribe(self, callback):
        if callback in self.subscribers:
            self.subscribers.remove(callback)


class ControlSocketTests(unittest.TestCase):
    """The widget's only interface. It has to answer, and answer honestly."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ear.sock"
        self.ear = _FakeEar()
        self.server = control.serve(self.ear, self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.shutdown)

    def _one(self, command):
        return next(control.request(command, path=self.path))

    def test_status_reports_without_changing_anything(self):
        self.assertEqual(self._one("status")["enabled"], False)
        self.assertFalse(self.ear.enabled)

    def test_toggle_changes_the_ear_and_returns_the_result(self):
        self.assertTrue(self._one("toggle")["enabled"])
        self.assertTrue(self.ear.enabled)

    def test_on_and_off_are_absolute(self):
        self._one("on")
        self.assertTrue(self.ear.enabled)
        self._one("on")
        self.assertTrue(self.ear.enabled)
        self._one("off")
        self.assertFalse(self.ear.enabled)

    def test_unknown_command_is_rejected(self):
        self.assertIn("error", self._one("rm -rf /"))

    def test_socket_is_private_to_this_user(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_stream_sends_the_snapshot_then_live_changes(self):
        received = []
        ready = threading.Event()

        def reader():
            for payload in control.request("stream", path=self.path, stream=True):
                received.append(payload)
                ready.set()
                if len(received) >= 2:
                    return

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=5), "no opening snapshot")

        self.ear.enabled = True
        for callback in list(self.ear.subscribers):
            callback(self.ear.snapshot())
        thread.join(timeout=5)

        self.assertGreaterEqual(len(received), 2)
        self.assertEqual(received[0]["state"], OFF)
        self.assertEqual(received[-1]["state"], LISTENING)

    def test_a_stale_socket_file_does_not_block_startup(self):
        # A crashed daemon leaves the file behind; the next start must reclaim it.
        second = _FakeEar()
        server = control.serve(second, self.path)
        self.addCleanup(server.shutdown)
        self.assertEqual(self._one("status")["enabled"], False)


if __name__ == "__main__":
    unittest.main()
