"""The Servitor's voice: Kokoro through a subprocess, then the vox chain.

Piper stays as the fallback. It is faster and never fails, but it is also
plainly a text-to-speech engine; Kokoro is close enough to a real voice that
the machine processing on top of it reads as deliberate rather than as a
limitation.
"""
from __future__ import annotations

import json
import logging
import subprocess  # nosec B404 - fixed argv, no shell; see _worker()
import threading
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
from ear.spoken_text import to_spoken
from ear.voice_fx import PROFILES, VoxProfile, apply_vox

logger = logging.getLogger(__name__)

# Kokoro ships one set of voices per language; an English voice reading
# Portuguese is worse than a plain one. Whisper tells us which was spoken,
# so the reply is synthesized by a speaker that actually knows the language.
# Anything not listed falls back to the configured default.
VOICE_BY_LANGUAGE = {
    "en": ("af_heart", "en-us"),
    "pt": ("pf_dora", "pt-br"),
    "es": ("ef_dora", "es"),
    "fr": ("ff_siwis", "fr-fr"),
    "it": ("if_sara", "it"),
    "hi": ("hf_alpha", "hi"),
    "ja": ("jf_alpha", "ja"),
    "zh": ("zf_xiaoxiao", "cmn"),
}


def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


class KokoroVoice:
    """Keeps one worker alive and talks to it a line at a time."""

    def __init__(
        self,
        python: Path,
        model: Path,
        voices: Path,
        voice: str = "af_heart",
        profile: VoxProfile | None = None,
        lang: str = "en-us",
    ):
        self.python = Path(python)
        self.model = Path(model)
        self.voices = Path(voices)
        self.voice = voice
        self.profile = profile or PROFILES["heavy"]
        self.lang = lang
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.python.is_file() and self.model.is_file() and self.voices.is_file()

    def _worker(self) -> subprocess.Popen:
        if self._process is not None and self._process.poll() is None:
            return self._process
        worker = Path(__file__).with_name("tts_worker.py")
        logger.info(f"[Voice] starting Kokoro worker under {self.python}")
        # Every element of this argv comes from configuration, not from
        # anything spoken or transcribed: the interpreter, this package's own
        # worker script, and two model paths. The text to synthesize is sent
        # over stdin as JSON and never reaches the command line. shell is
        # false, so nothing is word-split or expanded.
        self._process = subprocess.Popen(  # nosec B603 - argv is config-only, shell=False
            [str(self.python), str(worker), str(self.model), str(self.voices)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        handshake = json.loads(self._process.stdout.readline() or "{}")
        if not handshake.get("ready"):
            raise RuntimeError(f"Kokoro worker did not start: {handshake}")
        logger.info("[Voice] Kokoro worker ready")
        return self._process

    def warm(self) -> None:
        self._worker()

    def _for_language(self, language: str | None) -> tuple[str, str]:
        if not language:
            return self.voice, self.lang
        return VOICE_BY_LANGUAGE.get(language.lower(), (self.voice, self.lang))

    def synthesize(self, text: str, language: str | None = None) -> bytes:
        # Both engines get spoken text, never markup: a reply that came from
        # the MCP agent carries bullets and emphasis no matter what the prompt
        # asked for, and the synthesiser would read them out.
        text = to_spoken(text)
        voice, lang = self._for_language(language)
        # Lowering the pitch by resampling also lengthens the audio, so ask for
        # speech that much faster and the two cancel out.
        speed = 1.0 / max(0.5, self.profile.pitch_ratio)
        request = {"text": text, "voice": voice, "speed": speed, "lang": lang}

        with self._lock:
            worker = self._worker()
            worker.stdin.write(json.dumps(request) + "\n")
            worker.stdin.flush()
            reply = json.loads(worker.stdout.readline() or "{}")

        if "error" in reply:
            raise RuntimeError(reply["error"])
        path = Path(reply["path"])
        with wave.open(str(path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            raw = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16)
        path.unlink(missing_ok=True)

        audio = raw.astype(np.float32) / 32768.0
        return _to_wav_bytes(apply_vox(audio, sample_rate, self.profile), sample_rate)

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.stdin.close()
            self._process.terminate()
        self._process = None


class PiperVoiceFallback:
    """Piper, with the same vox chain, for when Kokoro is not installed."""

    def __init__(self, voice_path: Path, profile: VoxProfile | None = None):
        self.voice_path = Path(voice_path)
        self.profile = profile or PROFILES["heavy"]
        self._voice = None

    def available(self) -> bool:
        return self.voice_path.is_file()

    def warm(self) -> None:
        self._load()

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice as _PiperVoice

            self._voice = _PiperVoice.load(str(self.voice_path))
            logger.info(f"[Voice] Piper loaded from {self.voice_path}")
        return self._voice

    def synthesize(self, text: str, language: str | None = None) -> bytes:
        # Piper voices are per-file, so the fallback speaks one language only.
        text = to_spoken(text)
        voice = self._load()
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        buffer.seek(0)
        with wave.open(buffer, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            raw = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16)
        audio = raw.astype(np.float32) / 32768.0
        return _to_wav_bytes(apply_vox(audio, sample_rate, self.profile), sample_rate)

    def close(self) -> None:
        self._voice = None


def build(config) -> object:
    """Pick the best voice that is actually installed."""
    profile = PROFILES.get(config.vox_profile, PROFILES["heavy"])
    kokoro = KokoroVoice(
        python=config.tts_python,
        model=config.kokoro_model,
        voices=config.kokoro_voices,
        voice=config.tts_voice,
        profile=profile,
    )
    if kokoro.available():
        return kokoro
    logger.warning(
        f"[Voice] Kokoro not installed ({config.tts_python}); falling back to Piper. "
        f"Run scripts/install_tts.sh to get the better voice."
    )
    return PiperVoiceFallback(config.voice, profile)
