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
from unittest import mock
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
from ear.brain import LocalBrain  # noqa: E402
from ear.devices import (  # noqa: E402
    connected_displays,
    fits_on_shared_gpu,
    guard_device,
    model_weight_bytes,
)
from ear.transcribe import Transcript, wav_to_float32  # noqa: E402
from ear.voice_fx import PROFILES, VoxProfile, apply_vox  # noqa: E402

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
    "EAR_VOX_PROFILE": "",
    "EAR_TTS_VOICE": "",
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


class VoxEffectTests(unittest.TestCase):
    """The Servitor has to sound like a machine and still be understood."""

    def setUp(self):
        self.sr = 24000
        t = np.arange(self.sr, dtype=np.float32) / self.sr
        # A vowel-ish tone with harmonics stands in for speech.
        self.audio = (0.5 * np.sin(2 * np.pi * 180 * t)
                      + 0.3 * np.sin(2 * np.pi * 540 * t)).astype(np.float32)

    def test_output_stays_within_range(self):
        for name, profile in PROFILES.items():
            out = apply_vox(self.audio, self.sr, profile)
            self.assertLessEqual(float(np.abs(out).max()), 1.0, name)

    def test_output_is_never_silent(self):
        for name, profile in PROFILES.items():
            out = apply_vox(self.audio, self.sr, profile)
            self.assertGreater(float(np.abs(out).mean()), 0.001, name)

    def test_empty_audio_survives(self):
        self.assertEqual(apply_vox(np.zeros(0, dtype=np.float32), self.sr).size, 0)

    def test_speech_band_is_preserved(self):
        """Intelligibility check: energy must remain where consonants live."""
        out = apply_vox(self.audio, self.sr, PROFILES["heavy"])
        spectrum = np.abs(np.fft.rfft(out))
        freqs = np.fft.rfftfreq(len(out), 1 / self.sr)
        band = spectrum[(freqs >= 300) & (freqs <= 3400)].sum()
        self.assertGreater(band / spectrum.sum(), 0.30)

    def test_intensity_zero_leaves_the_voice_recognisable(self):
        out = apply_vox(self.audio, self.sr, PROFILES["off"])
        self.assertEqual(len(out), len(self.audio))

    def test_heavier_profiles_lower_the_pitch(self):
        self.assertLess(PROFILES["heavy"].pitch_ratio, PROFILES["magos"].pitch_ratio)
        self.assertLess(PROFILES["magos"].pitch_ratio, PROFILES["subtle"].pitch_ratio)

    def test_intensity_is_clamped_not_trusted(self):
        out = apply_vox(self.audio, self.sr, VoxProfile(intensity=9.0))
        self.assertLessEqual(float(np.abs(out).max()), 1.0)


class VoiceRoutingTests(unittest.TestCase):
    def test_the_default_profile_is_the_one_vitor_chose(self):
        with _env():
            self.assertEqual(EarConfig.from_env().vox_profile, "heavy")

    def test_language_selects_a_matching_voice(self):
        from ear.speak import KokoroVoice

        voice = KokoroVoice(Path("/nope"), Path("/nope"), Path("/nope"))
        self.assertEqual(voice._for_language("pt")[0], "pf_dora")
        self.assertEqual(voice._for_language("en")[0], "af_heart")

    def test_unknown_language_falls_back_to_the_default_voice(self):
        from ear.speak import KokoroVoice

        voice = KokoroVoice(Path("/nope"), Path("/nope"), Path("/nope"), voice="am_onyx")
        self.assertEqual(voice._for_language("kl")[0], "am_onyx")
        self.assertEqual(voice._for_language(None)[0], "am_onyx")


class TranscriptTests(unittest.TestCase):
    def test_empty_transcript_is_falsey(self):
        self.assertFalse(Transcript(""))
        self.assertTrue(Transcript("hello"))

    def test_language_defaults_to_english(self):
        self.assertEqual(Transcript("hi").language, "en")


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


class GpuSizeGuardTests(unittest.TestCase):
    """The GPU is shared with the compositor, so only a small model may use it.

    Measured on this machine: the driver reports a 4.00 GiB single-allocation
    ceiling, qwen3-4b weighs 2.27 GB and runs, qwen3-8b weighs 4.75 GB and the
    session took an i915 GPU HANG.
    """

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.drm = self.root / "drm"
        (self.drm / "card1" / "card1-eDP-1").mkdir(parents=True)
        (self.drm / "card1" / "card1-eDP-1" / "status").write_text("connected\n", encoding="utf-8")

    def _model(self, name: str, size: int) -> Path:
        model = self.root / name
        model.mkdir()
        (model / "openvino_model.bin").write_bytes(b"\0" * size)
        return model

    def test_weights_are_measured_from_the_bin_files(self):
        model = self._model("small", 1024)
        self.assertEqual(model_weight_bytes(model), 1024)

    def test_a_missing_directory_weighs_nothing(self):
        self.assertEqual(model_weight_bytes(self.root / "nope"), 0)

    def test_an_unsized_model_is_refused(self):
        fits, detail = fits_on_shared_gpu(None)
        self.assertFalse(fits)
        self.assertIn("no model directory", detail)

    def test_a_model_under_the_ceiling_keeps_the_gpu(self):
        model = self._model("small", 2048)
        with _env(), mock.patch("ear.devices.gpu_max_alloc_bytes", return_value=100_000):
            self.assertEqual(guard_device("GPU", "x", self.drm, model_dir=model), "GPU")

    def test_a_model_over_the_ceiling_is_sent_to_the_npu(self):
        model = self._model("big", 90_000)
        with _env(), mock.patch("ear.devices.gpu_max_alloc_bytes", return_value=100_000):
            self.assertEqual(guard_device("GPU", "x", self.drm, model_dir=model), "NPU")

    def test_an_unreadable_ceiling_is_not_treated_as_unlimited(self):
        model = self._model("small", 8)
        with _env(), mock.patch("ear.devices.gpu_max_alloc_bytes", return_value=0):
            self.assertEqual(guard_device("GPU", "x", self.drm, model_dir=model), "NPU")


class ThinkingBlockTests(unittest.TestCase):
    """Qwen3 wraps its reasoning in literal <think> tags, per chat_template.jinja."""

    def test_the_answer_after_the_block_is_kept(self):
        self.assertEqual(
            LocalBrain._strip_thinking("<think>\nwhy\n</think>\n\nIt is six."), "It is six."
        )

    def test_a_plain_multi_line_answer_survives_intact(self):
        text = "The server is up.\nIt has run for three days."
        self.assertEqual(LocalBrain._strip_thinking(text), text)

    def test_an_unterminated_block_speaks_nothing(self):
        self.assertEqual(LocalBrain._strip_thinking("<think>\nstill reasoning"), "")

    def test_the_word_thinking_in_an_answer_is_not_a_marker(self):
        text = "I am thinking\nabout your request."
        self.assertEqual(LocalBrain._strip_thinking(text), text)


class IdleUnloadTests(unittest.TestCase):
    """The model is kept for a conversation and dropped after it, not per turn."""

    def _brain(self):
        brain = LocalBrain.__new__(LocalBrain)
        brain.model_dir = Path("model")
        brain.device = "GPU"
        brain._pipeline = object()
        brain._history = []
        brain._last_used = 0.0
        return brain

    def test_nothing_is_dropped_while_the_model_is_still_in_use(self):
        brain = self._brain()
        with mock.patch("ear.brain.time.monotonic", return_value=10.0):
            brain._last_used = 5.0
            self.assertFalse(brain.unload_if_idle(600.0))
        self.assertIsNotNone(brain._pipeline)

    def test_the_model_is_dropped_once_the_room_has_been_quiet(self):
        brain = self._brain()
        with mock.patch("ear.brain.time.monotonic", return_value=1000.0):
            brain._last_used = 100.0
            self.assertTrue(brain.unload_if_idle(600.0))
        self.assertIsNone(brain._pipeline)

    def test_a_zero_timeout_keeps_the_model_loaded_forever(self):
        brain = self._brain()
        with mock.patch("ear.brain.time.monotonic", return_value=1e9):
            self.assertFalse(brain.unload_if_idle(0.0))
        self.assertIsNotNone(brain._pipeline)

    def test_unloading_an_already_unloaded_model_is_harmless(self):
        brain = self._brain()
        brain._pipeline = None
        self.assertFalse(brain.unload_if_idle(1.0))


class SpoolRetentionTests(unittest.TestCase):
    """Recordings of a home microphone are not kept forever."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spool = Path(self.tmp.name)

    def _ear(self, keep: int):
        config = EarConfig(spool_dir=self.spool, spool_keep=keep)
        return ServitorEar(config)

    def _recording(self, name: str, mtime: float) -> Path:
        path = self.spool / name
        path.write_bytes(b"RIFF")
        os.utime(path, (mtime, mtime))
        return path

    def test_only_the_newest_recordings_survive(self):
        for index in range(6):
            self._recording(f"command-{index}.wav", 1000.0 + index)
        self._ear(3)._trim_spool()
        left = sorted(p.name for p in self.spool.glob("command-*.wav"))
        self.assertEqual(left, ["command-3.wav", "command-4.wav", "command-5.wav"])

    def test_a_spool_under_the_cap_is_left_alone(self):
        self._recording("command-0.wav", 1000.0)
        self._ear(3)._trim_spool()
        self.assertEqual(len(list(self.spool.glob("command-*.wav"))), 1)

    def test_a_zero_cap_keeps_nothing(self):
        self._recording("command-0.wav", 1000.0)
        self._ear(0)._trim_spool()
        self.assertEqual(list(self.spool.glob("command-*.wav")), [])

    def test_files_that_are_not_recordings_are_never_touched(self):
        keeper = self.spool / "notes.txt"
        keeper.write_text("mine", encoding="utf-8")
        for index in range(5):
            self._recording(f"command-{index}.wav", 1000.0 + index)
        self._ear(1)._trim_spool()
        self.assertTrue(keeper.exists())

    def test_a_missing_spool_directory_is_not_an_error(self):
        config = EarConfig(spool_dir=self.spool / "gone", spool_keep=3)
        ServitorEar(config)._trim_spool()

    def test_the_cap_is_configurable(self):
        with _env(EAR_SPOOL_KEEP="7"):
            self.assertEqual(EarConfig.from_env().spool_keep, 7)


class WakeCommitTests(unittest.TestCase):
    """A partial decode is a hypothesis, not a wake."""

    class _Recognizer:
        """Stands in for Kaldi: says whether each chunk closed an utterance."""

        def __init__(self, script):
            self.script = list(script)

        def AcceptWaveform(self, chunk):  # noqa: N802 - vosk's own spelling
            self.final, self.text = self.script.pop(0)
            return self.final

        def Result(self):  # noqa: N802
            return json.dumps({"text": self.text})

        def PartialResult(self):  # noqa: N802
            return json.dumps({"partial": self.text})

    def _ear(self, require_final: bool):
        return ServitorEar(EarConfig(wake_phrase="hey oracle", wake_require_final=require_final))

    def test_a_committed_result_wakes_the_ear(self):
        rec = self._Recognizer([(True, "hey oracle")])
        self.assertTrue(self._ear(True)._decode_for_wake(rec, [b"\0" * 3200]))

    def test_a_partial_alone_does_not_wake_it(self):
        # "ok then" matched the phrase as a partial and never as a final.
        rec = self._Recognizer([(False, "hey oracle"), (True, "")])
        self.assertFalse(self._ear(True)._decode_for_wake(rec, [b"\0" * 3200] * 2))

    def test_partials_still_count_when_the_guard_is_turned_off(self):
        rec = self._Recognizer([(False, "hey oracle")])
        self.assertTrue(self._ear(False)._decode_for_wake(rec, [b"\0" * 3200]))

    def test_unrelated_speech_never_wakes_it(self):
        rec = self._Recognizer([(True, "[unk]"), (True, "")])
        self.assertFalse(self._ear(True)._decode_for_wake(rec, [b"\0" * 3200] * 2))

    def test_the_guard_is_configurable(self):
        with _env(EAR_WAKE_REQUIRE_FINAL="false"):
            self.assertFalse(EarConfig.from_env().wake_require_final)
        with _env():
            self.assertTrue(EarConfig.from_env().wake_require_final)

    def test_a_breath_no_longer_ends_a_command(self):
        # A pause for air is comfortably under two seconds.
        self.assertGreaterEqual(EarConfig().silence_seconds, 2.0)


class FollowUpTurnTests(unittest.TestCase):
    """A conversation should not need the wake phrase at every step."""

    class _Responder:
        def __init__(self, replies):
            self.replies = list(replies)
            self.calls = 0

        def __call__(self, wav):
            self.calls += 1
            return self.replies.pop(0) if self.replies else None

    def _ear(self, responder, **overrides):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = EarConfig(spool_dir=Path(tmp.name), **overrides)
        ear = ServitorEar(config, responder=responder)
        ear._speak_wav = lambda *a, **k: None
        ear._speak_text = lambda *a, **k: None
        ear._ack_audio = lambda: b""
        ear._drain = lambda stream: None
        return ear

    def _commands(self, ear, captures):
        """Feed _record_command a fixed script of turns."""
        script = list(captures)
        seen = []

        def record(stream, lead_in_seconds=None):
            seen.append(lead_in_seconds)
            return script.pop(0) if script else None

        ear._record_command = record
        return seen

    def test_a_second_command_is_taken_without_the_wake_phrase(self):
        responder = self._Responder(["one", "two"])
        ear = self._ear(responder)
        self._commands(ear, [b"RIFF1", b"RIFF2", None])
        ear._handle_wake(object())
        self.assertEqual(responder.calls, 2)

    def test_the_follow_up_window_is_shorter_than_the_first(self):
        responder = self._Responder(["one", "two"])
        ear = self._ear(responder, lead_in_seconds=5.0, followup_seconds=6.0)
        seen = self._commands(ear, [b"RIFF1", b"RIFF2", None])
        ear._handle_wake(object())
        self.assertEqual(seen[0], 5.0)
        self.assertEqual(seen[1], 6.0)

    def test_silence_ends_the_conversation(self):
        responder = self._Responder(["one"])
        ear = self._ear(responder)
        self._commands(ear, [b"RIFF1", None])
        ear._handle_wake(object())
        self.assertEqual(responder.calls, 1)

    def test_follow_ups_are_bounded(self):
        responder = self._Responder(["a", "b", "c", "d", "e", "f"])
        ear = self._ear(responder, followup_turns=2)
        self._commands(ear, [b"1", b"2", b"3", b"4", b"5"])
        ear._handle_wake(object())
        self.assertEqual(responder.calls, 3)  # the first, plus two follow-ups

    def test_zero_seconds_turns_the_whole_thing_off(self):
        responder = self._Responder(["one", "two"])
        ear = self._ear(responder, followup_seconds=0.0)
        self._commands(ear, [b"RIFF1", b"RIFF2"])
        ear._handle_wake(object())
        self.assertEqual(responder.calls, 1)

    def test_saying_nothing_at_all_answers_nothing(self):
        responder = self._Responder(["one"])
        ear = self._ear(responder)
        self._commands(ear, [None])
        ear._handle_wake(object())
        self.assertEqual(responder.calls, 0)

    def test_the_window_is_configurable(self):
        with _env(EAR_FOLLOWUP_SECONDS="0", EAR_FOLLOWUP_TURNS="9"):
            config = EarConfig.from_env()
            self.assertEqual(config.followup_seconds, 0.0)
            self.assertEqual(config.followup_turns, 9)
