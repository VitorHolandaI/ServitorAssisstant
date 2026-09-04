"""Wake-word listener and its state machine.

Everything here runs on the local machine and touches no network. The wake
gate is deliberately the cheapest stage in the system: a Vosk recognizer
restricted to a two-word grammar decodes continuously at a measured realtime
factor of ~0.018, so leaving it on all day costs about two percent of one core.
Only after the phrase is heard does anything expensive happen.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_RATE = 16000
BLOCK_FRAMES = 1600  # 100 ms — fine enough for silence detection, cheap for Vosk

# State names are part of the protocol the bar widget reads. Keep them stable.
OFF = "off"
LISTENING = "listening"
AWAKE = "awake"
RECORDING = "recording"
THINKING = "thinking"
SPEAKING = "speaking"
ERROR = "error"


@dataclass(frozen=True)
class EarConfig:
    """Everything tunable, resolved once at startup."""

    wake_phrase: str = "hey oracle"
    vosk_model: Path = REPO_ROOT / "voice_models" / "vosk-model-small-en-us-0.15"
    voice: Path = REPO_ROOT / "voice_models" / "en_US-ryan-medium.onnx"
    # Kokoro sounds markedly more natural than Piper, but needs numpy 2.x,
    # which gruut forbids in this environment. It therefore runs in its own
    # virtualenv, driven as a subprocess. See api/ear/speak.py.
    tts_python: Path = REPO_ROOT / ".venv-tts" / "bin" / "python"
    kokoro_model: Path = REPO_ROOT / "voice_models" / "kokoro-v1.0.onnx"
    kokoro_voices: Path = REPO_ROOT / "voice_models" / "voices-v1.0.bin"
    tts_voice: str = "af_heart"
    # Vox-caster processing applied to whatever voice is used: off, subtle,
    # magos, heavy. See api/ear/voice_fx.py.
    vox_profile: str = "heavy"
    ack_text: str = "I'm here."
    spool_dir: Path = Path.home() / ".cache" / "servitor" / "spool"
    whisper_model: Path = REPO_ROOT / "voice_models" / "whisper-base-fp16-ov"
    whisper_device: str = "NPU"
    llm_model: Path = REPO_ROOT / "voice_models" / "qwen2.5-1.5b-instruct-int4-ov"
    llm_device: str = "NPU"
    silence_seconds: float = 1.2
    max_command_seconds: float = 15.0
    min_command_seconds: float = 0.4
    # How long to wait for you to start talking after the acknowledgement.
    # A person needs about a second; the old behaviour started counting
    # silence immediately and closed the recording before they began.
    lead_in_seconds: float = 5.0
    # Speech has to persist for this long to count as speech at all, so the
    # tail of our own acknowledgement leaking back into the microphone does
    # not open and then immediately close a recording.
    speech_onset_seconds: float = 0.2
    # Silence after playback before we trust the microphone again.
    settle_seconds: float = 0.4
    # Stage zero: only run the decoder while there is sound worth decoding.
    # Commercial wake-word stacks are built the same way round — the cheapest
    # thing runs always, and wakes the next stage up.
    vad_enabled: bool = True
    # Audio kept from before speech was detected, so the decoder still hears
    # the first syllable of the phrase rather than starting mid-word.
    vad_preroll_seconds: float = 0.4
    # How long to keep decoding after the room goes quiet again.
    vad_hangover_seconds: float = 0.8
    start_enabled: bool = True

    @classmethod
    def from_env(cls) -> EarConfig:
        # An env var that is present but empty means "unset" here. A blank line
        # in .env must fall back to the default, not configure an empty device.
        def str_env(name: str, default: str) -> str:
            return os.getenv(name, "").strip() or default

        def path_env(name: str, default: Path) -> Path:
            raw = os.getenv(name, "").strip()
            return Path(raw).expanduser() if raw else default

        defaults = cls()
        return cls(
            wake_phrase=str_env("EAR_WAKE_PHRASE", defaults.wake_phrase).lower(),
            vosk_model=path_env("EAR_VOSK_MODEL", defaults.vosk_model),
            voice=path_env("EAR_VOICE", defaults.voice),
            ack_text=str_env("EAR_ACK_TEXT", defaults.ack_text),
            tts_python=path_env("EAR_TTS_PYTHON", defaults.tts_python),
            kokoro_model=path_env("EAR_KOKORO_MODEL", defaults.kokoro_model),
            kokoro_voices=path_env("EAR_KOKORO_VOICES", defaults.kokoro_voices),
            tts_voice=str_env("EAR_TTS_VOICE", defaults.tts_voice),
            vox_profile=str_env("EAR_VOX_PROFILE", defaults.vox_profile).lower(),
            whisper_model=path_env("EAR_WHISPER_MODEL", defaults.whisper_model),
            whisper_device=str_env("EAR_WHISPER_DEVICE", defaults.whisper_device).upper(),
            llm_model=path_env("EAR_LLM_MODEL", defaults.llm_model),
            llm_device=str_env("EAR_LLM_DEVICE", defaults.llm_device).upper(),
            spool_dir=path_env("EAR_SPOOL_DIR", defaults.spool_dir),
            silence_seconds=float(str_env("EAR_SILENCE_SECONDS", str(defaults.silence_seconds))),
            max_command_seconds=float(str_env("EAR_MAX_COMMAND_SECONDS", str(defaults.max_command_seconds))),
            min_command_seconds=float(str_env("EAR_MIN_COMMAND_SECONDS", str(defaults.min_command_seconds))),
            lead_in_seconds=float(str_env("EAR_LEAD_IN_SECONDS", str(defaults.lead_in_seconds))),
            speech_onset_seconds=float(str_env("EAR_SPEECH_ONSET_SECONDS", str(defaults.speech_onset_seconds))),
            settle_seconds=float(str_env("EAR_SETTLE_SECONDS", str(defaults.settle_seconds))),
            vad_enabled=str_env("EAR_VAD", "true").lower() != "false",
            vad_preroll_seconds=float(str_env("EAR_VAD_PREROLL_SECONDS", str(defaults.vad_preroll_seconds))),
            vad_hangover_seconds=float(str_env("EAR_VAD_HANGOVER_SECONDS", str(defaults.vad_hangover_seconds))),
            start_enabled=os.getenv("EAR_START_ENABLED", "true").lower() != "false",
        )


class _EnergyGate:
    """Stage zero: decide whether the decoder gets to see this block at all.

    Keeps a short pre-roll so that when it does open, the decoder still hears
    the run-up to the phrase instead of starting mid-word.
    """

    def __init__(self, preroll_blocks: int, hangover_blocks: int):
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_blocks))
        self._hangover = max(1, hangover_blocks)
        self._quiet = self._hangover  # start closed
        self.just_closed = False

    @property
    def open(self) -> bool:
        return self._quiet < self._hangover

    def feed(self, block: bytes, loud: bool) -> list[bytes]:
        """Blocks the decoder should consume now; empty means stay asleep."""
        self._quiet = 0 if loud else self._quiet + 1
        self.just_closed = self._quiet == self._hangover
        if not self.open:
            self._preroll.append(block)
            return []
        if self._preroll:
            replay = [*self._preroll, block]
            self._preroll.clear()
            return replay
        return [block]

    def reset(self) -> None:
        self._preroll.clear()
        self._quiet = self._hangover
        self.just_closed = False


def _rms(block: bytes) -> float:
    samples = np.frombuffer(block, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


class ServitorEar:
    """Listens for the wake phrase, then records one command and answers it.

    A `responder` receives the recorded WAV bytes and returns either text to
    speak or None. Phase one ships without one: the ear wakes, acknowledges,
    captures the command and spools it to disk. That keeps the always-on half
    testable on its own, before a local model is wired to the other half.
    """

    def __init__(self, config: EarConfig, responder: Callable[[bytes], str | None] | None = None):
        self.config = config
        self.responder = responder

        self._state = OFF
        self._detail: str | None = None
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[dict], None]] = []

        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._voice = None  # built lazily; only needed once it has to speak
        self._ack_wav: bytes | None = None
        self._noise_floor = 200.0  # updated from real silence as it listens

        if config.start_enabled:
            self._enabled.set()

    # ---------------------------------------------------------------- state

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "enabled": self._enabled.is_set(),
                "wake_phrase": self.config.wake_phrase,
                "detail": self._detail,
                "ts": time.time(),
            }

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)
        # Same contract as _set_state: a subscriber that throws is the
        # subscriber's problem, never the listener's.
        try:
            callback(self.snapshot())
        except Exception as error:
            logger.debug(f"[Ear] subscriber failed on first snapshot: {error}")

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _set_state(self, state: str, detail: str | None = None) -> None:
        with self._lock:
            if state == self._state and detail == self._detail:
                return
            self._state = state
            self._detail = detail
            subscribers = list(self._subscribers)
        payload = self.snapshot()
        logger.info(f"[Ear] state={state}" + (f" detail={detail}" if detail else ""))
        for callback in subscribers:
            try:
                callback(payload)
            except Exception as error:  # a dead widget must not stop the ear
                logger.debug(f"[Ear] subscriber failed: {error}")

    # -------------------------------------------------------------- control

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def toggle(self) -> bool:
        enabled = not self._enabled.is_set()
        self.set_enabled(enabled)
        return enabled

    def start(self) -> None:
        self.config.spool_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="servitor-ear", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._enabled.set()  # release the wait in the disabled branch
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ----------------------------------------------------------------- loop

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._enabled.is_set():
                self._set_state(OFF)
                self._enabled.wait(timeout=0.5)
                continue
            try:
                self._listen_session()
            except Exception as error:
                logger.exception("[Ear] session failed")
                self._set_state(ERROR, str(error))
                time.sleep(2.0)

    def _listen_session(self) -> None:
        """Hold the microphone open for as long as the ear stays enabled."""
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        if not self.config.vosk_model.is_dir():
            raise FileNotFoundError(f"Vosk model not found: {self.config.vosk_model}")
        model = Model(str(self.config.vosk_model))

        # Restricting the grammar to the wake phrase is what makes always-on
        # affordable: the decoder can only ever emit the phrase or [unk].
        grammar = json.dumps([self.config.wake_phrase, "[unk]"])
        recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)

        blocks_per_second = SAMPLE_RATE / BLOCK_FRAMES
        gate = (
            _EnergyGate(
                int(self.config.vad_preroll_seconds * blocks_per_second),
                int(self.config.vad_hangover_seconds * blocks_per_second),
            )
            if self.config.vad_enabled
            else None
        )

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            dtype="int16",
            channels=1,
        ) as stream:
            self._set_state(LISTENING)
            while self._enabled.is_set() and not self._stop.is_set():
                block, _ = stream.read(BLOCK_FRAMES)
                block = bytes(block)
                self._track_noise_floor(block)

                chunks = [block]
                if gate is not None:
                    chunks = gate.feed(block, _rms(block) >= self._speech_threshold())
                    if gate.just_closed:
                        # Room went quiet: drop any half-formed hypothesis.
                        recognizer.Reset()
                    if not chunks:
                        continue

                if self._decode_for_wake(recognizer, chunks):
                    recognizer.Reset()
                    if gate is not None:
                        gate.reset()
                    self._handle_wake(stream)
                    self._set_state(LISTENING)

    def _decode_for_wake(self, recognizer, chunks: list[bytes]) -> bool:
        """Feed audio to the decoder, reporting whether the phrase appeared."""
        for chunk in chunks:
            if recognizer.AcceptWaveform(chunk):
                heard = json.loads(recognizer.Result()).get("text", "")
            else:
                heard = json.loads(recognizer.PartialResult()).get("partial", "")
            if self.config.wake_phrase in heard:
                return True
        return False

    def _speech_threshold(self) -> float:
        """Level a block must reach to count as someone talking."""
        return max(self._noise_floor * 3.0, 300.0)

    def _track_noise_floor(self, block: bytes) -> None:
        """Follow the room's quiet level so the silence threshold adapts to it."""
        level = _rms(block)
        if level < self._noise_floor * 1.5:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * level
        self._noise_floor = max(self._noise_floor, 50.0)

    # ----------------------------------------------------------------- turn

    def _handle_wake(self, stream) -> None:
        logger.info(f"[Ear] wake phrase heard: {self.config.wake_phrase!r}")
        self._set_state(AWAKE)
        self._speak_wav(self._ack_audio())
        self._drain(stream)

        self._set_state(RECORDING)
        command = self._record_command(stream)
        if command is None:
            logger.info("[Ear] no command followed the wake phrase")
            return

        spool_path = self.config.spool_dir / f"command-{int(time.time())}.wav"
        spool_path.write_bytes(command)
        logger.info(f"[Ear] command captured: {spool_path} ({len(command)} bytes)")

        if self.responder is None:
            return

        self._set_state(THINKING)
        try:
            reply = self.responder(command)
        except Exception as error:
            logger.exception("[Ear] responder failed")
            self._set_state(ERROR, str(error))
            return
        if reply:
            # A responder may answer with plain text or with a Reply carrying
            # the language it should be spoken in.
            self._speak_text(getattr(reply, "text", reply), getattr(reply, "language", None))

    def _drain(self, stream) -> None:
        """Throw away everything the microphone heard while we were talking.

        The speaker feeds back into the microphone, so without this the
        acknowledgement is itself recorded and transcribed. Measured once as a
        command of "Thank you." that the user never said.
        """
        time.sleep(self.config.settle_seconds)
        for _ in range(100):  # bounded; a stuck stream must not hang the ear
            available = getattr(stream, "read_available", 0)
            if available < BLOCK_FRAMES:
                break
            stream.read(BLOCK_FRAMES)

    def _record_command(self, stream) -> bytes | None:
        """Record until the speaker stops, capped by `max_command_seconds`.

        Two clocks run here: one waiting for speech to begin, one waiting for
        it to end. Collapsing them into a single silence counter is what made
        an earlier version close the recording before the user had started.
        """
        threshold = self._speech_threshold()
        blocks_per_second = SAMPLE_RATE / BLOCK_FRAMES
        silence_blocks = int(self.config.silence_seconds * blocks_per_second)
        lead_in_blocks = int(self.config.lead_in_seconds * blocks_per_second)
        onset_blocks = max(1, int(self.config.speech_onset_seconds * blocks_per_second))
        max_blocks = int(self.config.max_command_seconds * blocks_per_second)

        frames: list[bytes] = []
        loud_run = 0
        quiet_run = 0
        heard_speech = False
        waited = 0

        for _ in range(max_blocks):
            if not self._enabled.is_set() or self._stop.is_set():
                break
            block, _ = stream.read(BLOCK_FRAMES)
            block = bytes(block)
            loud = _rms(block) >= threshold

            if not heard_speech:
                waited += 1
                loud_run = loud_run + 1 if loud else 0
                if loud_run >= onset_blocks:
                    heard_speech = True
                    quiet_run = 0
                elif waited >= lead_in_blocks:
                    return None  # nothing followed the wake phrase
                # Keep the run-up so the first syllable is not clipped.
                frames.append(block)
                continue

            frames.append(block)
            if loud:
                quiet_run = 0
            else:
                quiet_run += 1
                if quiet_run >= silence_blocks:
                    break

        if not heard_speech:
            return None
        audio = b"".join(frames)
        if len(audio) < self.config.min_command_seconds * SAMPLE_RATE * 2:
            return None
        return _to_wav(audio)

    # ---------------------------------------------------------------- voice

    def _speaker(self):
        if self._voice is None:
            from ear import speak

            self._voice = speak.build(self.config)
        return self._voice

    def _synthesize(self, text: str, language: str | None = None) -> bytes:
        return self._speaker().synthesize(text, language)

    def _ack_audio(self) -> bytes:
        if self._ack_wav is None:
            self._ack_wav = self._synthesize(self.config.ack_text)
        return self._ack_wav

    def _speak_text(self, text: str, language: str | None = None) -> None:
        self._speak_wav(self._synthesize(text, language))

    def _speak_wav(self, wav_bytes: bytes) -> None:
        import sounddevice as sd
        import soundfile as sf

        self._set_state(SPEAKING)
        data, samplerate = sf.read(BytesIO(wav_bytes), dtype="float32")
        sd.play(data, samplerate)
        sd.wait()


def _to_wav(pcm: bytes) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return buffer.getvalue()
