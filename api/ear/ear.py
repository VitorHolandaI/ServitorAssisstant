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
    # How many command recordings to keep. They are kept at all so a bad
    # transcription can be listened to afterwards, which is only useful for
    # the last few; without a cap the directory grows for the life of the
    # install, holding every sentence ever spoken near the microphone.
    spool_keep: int = 3
    whisper_model: Path = REPO_ROOT / "voice_models" / "whisper-base-fp16-ov"
    whisper_device: str = "NPU"
    llm_model: Path = REPO_ROOT / "voice_models" / "qwen2.5-1.5b-instruct-int4-ov"
    llm_device: str = "NPU"
    # When set, bypasses the local LLM and calls the server's MCP agent instead.
    # The server runs the LangGraph ReAct agent with tools (weather, Nextcloud,
    # Home Assistant, etc). Format: "http://host:port" (default port 8000).
    server_url: str = ""
    # How long the room must stay quiet before the language model is dropped
    # from the accelerator. Measured on qwen3-4b: holding it costs ~975 MB Rss,
    # rebuilding it from a warm cache costs 3.8 s. Keeping it for the length of
    # a conversation and freeing it afterwards pays neither on a follow-up.
    idle_unload_seconds: float = 600.0
    # How long the room must stay quiet before a command counts as finished.
    # At 1.2s a pause for breath ended the sentence mid-thought. A breath is
    # comfortably under two seconds; a real end-of-turn pause is longer.
    silence_seconds: float = 2.0
    # Raised alongside it: a sentence with a breath in it is now allowed to
    # run to its end rather than being cut by the ceiling instead.
    max_command_seconds: float = 20.0
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
    # Only act on a decoder result the recogniser has committed to. A partial
    # is a running hypothesis that it is still free to withdraw, and it is
    # where the false wakes came from: "ok then" matched the phrase as a
    # partial and never as a final. Measured 1/12 vs 0/12 on spoken "ok"
    # variants, with true phrases still at 4/4 either way.
    wake_require_final: bool = True
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
            if not raw:
                return default
            p = Path(raw).expanduser()
            return p if p.is_absolute() else (REPO_ROOT / p).resolve()

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
            server_url=str_env("EAR_SERVER_URL", defaults.server_url),
            idle_unload_seconds=float(str_env("EAR_IDLE_UNLOAD_SECONDS", str(defaults.idle_unload_seconds))),
            spool_dir=path_env("EAR_SPOOL_DIR", defaults.spool_dir),
            spool_keep=int(str_env("EAR_SPOOL_KEEP", str(defaults.spool_keep))),
            silence_seconds=float(str_env("EAR_SILENCE_SECONDS", str(defaults.silence_seconds))),
            max_command_seconds=float(str_env("EAR_MAX_COMMAND_SECONDS", str(defaults.max_command_seconds))),
            min_command_seconds=float(str_env("EAR_MIN_COMMAND_SECONDS", str(defaults.min_command_seconds))),
            lead_in_seconds=float(str_env("EAR_LEAD_IN_SECONDS", str(defaults.lead_in_seconds))),
            speech_onset_seconds=float(str_env("EAR_SPEECH_ONSET_SECONDS", str(defaults.speech_onset_seconds))),
            settle_seconds=float(str_env("EAR_SETTLE_SECONDS", str(defaults.settle_seconds))),
            wake_require_final=str_env("EAR_WAKE_REQUIRE_FINAL", "true").lower() != "false",
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
        # Duck-typed on purpose: the ear works with any callable responder,
        # and only the ones that can report a transcript get asked for one.
        if hasattr(responder, "on_heard"):
            responder.on_heard = self._on_heard

        self._state = OFF
        self._detail: str | None = None
        self._heard = ""
        self._reply = ""
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
                # What this turn heard and answered, for anything drawing a
                # transcript. Empty between turns rather than stale.
                "heard": self._heard,
                "reply": self._reply,
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

    def _on_heard(self, heard: str) -> None:
        """The transcript, as soon as it exists - before the model answers."""
        self._set_turn(heard, "")

    def _set_turn(self, heard: str = "", reply: str = "") -> None:
        """Record the current turn's transcript, and push it to subscribers.

        Kept separate from the state so a widget can show what was said while
        the state moves on from thinking to speaking underneath it.
        """
        with self._lock:
            if heard == self._heard and reply == self._reply:
                return
            self._heard = heard
            self._reply = reply
            subscribers = list(self._subscribers)
        payload = self.snapshot()
        for callback in subscribers:
            try:
                callback(payload)
            except Exception as error:
                logger.debug(f"[Ear] subscriber failed: {error}")

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

                # Housekeeping runs on this thread, between blocks, so it can
                # never free a model out from under a generate in progress. It
                # has to sit above the gate: a quiet room `continue`s past
                # everything below, and a quiet room is exactly when the
                # language model should be let go.
                tick = getattr(self.responder, "tick", None)
                if tick is not None:
                    tick()

                chunks = [block]
                if gate is not None:
                    chunks = gate.feed(block, _rms(block) >= self._speech_threshold())
                    if gate.just_closed:
                        # Room went quiet. Ask the decoder to commit first:
                        # this is the moment it would have emitted its final
                        # result, and resetting straight through it is what
                        # made the committed-result rule never fire at all.
                        if self._flush_for_wake(recognizer):
                            gate.reset()
                            self._handle_wake(stream)
                            self._set_state(LISTENING)
                            continue
                        recognizer.Reset()
                    if not chunks:
                        continue

                if self._decode_for_wake(recognizer, chunks):
                    recognizer.Reset()
                    if gate is not None:
                        gate.reset()
                    self._handle_wake(stream)
                    self._set_state(LISTENING)

    def _flush_for_wake(self, recognizer) -> bool:
        """Close the utterance and report whether the phrase was committed.

        The energy gate ends an utterance by going quiet, not by handing the
        decoder silence, so nothing else makes it commit. Without this the
        wake phrase stays a partial forever and is then discarded by Reset.
        """
        heard = json.loads(recognizer.FinalResult()).get("text", "")
        return self.config.wake_phrase in heard

    def _decode_for_wake(self, recognizer, chunks: list[bytes]) -> bool:
        """Feed audio to the decoder, reporting whether the phrase appeared.

        A partial result is a hypothesis the recogniser has not committed to
        and may still withdraw. Trusting them is what let "ok then" wake the
        Servitor: the grammar can only emit the phrase or [unk], so a short
        ambiguous sound briefly lands on the phrase before the decoder settles
        on [unk]. Waiting for the committed result costs the pause at the end
        of the phrase and removes that whole class of false wake.
        """
        for chunk in chunks:
            if recognizer.AcceptWaveform(chunk):
                heard = json.loads(recognizer.Result()).get("text", "")
            elif self.config.wake_require_final:
                continue
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
        self._set_turn()
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
        self._trim_spool()

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
            text = getattr(reply, "text", reply)
            self._set_turn(getattr(reply, "heard", ""), str(text))
            self._speak_text(text, getattr(reply, "language", None))

    def _trim_spool(self) -> None:
        """Keep only the newest few recordings.

        These are recordings of a microphone in someone's home. Keeping the
        last few is a debugging aid; keeping all of them forever is a
        liability, so the cap is enforced on every capture rather than by
        anything the user has to remember to run.
        """
        keep = max(0, self.config.spool_keep)
        try:
            recordings = sorted(
                self.config.spool_dir.glob("command-*.wav"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError as error:
            logger.debug(f"[Ear] could not list the spool: {error}")
            return
        for stale in recordings[keep:]:
            try:
                stale.unlink()
            except OSError as error:
                logger.debug(f"[Ear] could not remove {stale}: {error}")
        if len(recordings) > keep:
            logger.info(f"[Ear] spool trimmed to {keep} recording(s)")

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
