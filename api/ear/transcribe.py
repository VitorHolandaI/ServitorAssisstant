"""Speech to text on the Intel NPU.

The NPU carries a state bug: an inference whose prompt is a single token
returns the *previous* call's result unless it is the first inference on that
request. Whisper's language detection is exactly such a one-token inference, so
a long-lived pipeline transcribes the second utterance in the first
utterance's language — silently, with no error.

Measured on this machine with `whisper-base-fp16-ov`, alternating English and
Portuguese on one pipeline: the Portuguese clip came back as
"and please give it a thumbs up." and the English one came back in Portuguese.
Building a fresh pipeline per utterance makes every inference the first one,
and all four clips then came back in the right language.

A fresh pipeline costs ~0.8s once the compiled blob is cached, against ~0.3s to
transcribe. That is the price of being able to speak any language, and it buys
the NPU, which draws far less power than the GPU for the same work.
"""
from __future__ import annotations

import logging
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
from ear.devices import guard_device

logger = logging.getLogger(__name__)


def wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    """Decode mono 16-bit PCM WAV into the float32 array Whisper expects."""
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        if wav_file.getsampwidth() != 2 or wav_file.getnchannels() != 1:
            raise ValueError(
                f"expected mono 16-bit PCM, got channels={wav_file.getnchannels()} "
                f"sampwidth={wav_file.getsampwidth()}"
            )
        frames = wav_file.readframes(wav_file.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


class OpenVinoWhisper:
    """Transcribe an utterance, in whatever language it happens to be in."""

    def __init__(self, model_dir: Path, device: str = "NPU", cache_dir: Path | None = None):
        self.model_dir = Path(model_dir)
        self.device = guard_device(device, "Whisper")
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "servitor" / "ov-cache"
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"Whisper model not found: {self.model_dir}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def warm(self) -> None:
        """Pay the one-off compile now, so the first real utterance is not slow."""
        self._pipeline()

    def _pipeline(self):
        import openvino_genai as ov_genai

        return ov_genai.WhisperPipeline(
            str(self.model_dir), self.device, CACHE_DIR=str(self.cache_dir)
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        audio = wav_to_float32(wav_bytes)
        if audio.size == 0:
            return ""
        # Built and dropped per call on purpose — see the module docstring.
        pipeline = self._pipeline()
        try:
            return str(pipeline.generate(audio)).strip()
        finally:
            del pipeline
