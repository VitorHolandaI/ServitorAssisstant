"""Baseline strategy: Vosk directly, no preprocessing.

Reference point for every other experiment in this folder. Kept close
to `api/server/Server.py:process_audio` semantically, but uses `vosk`
directly so the model path is configurable via env.

Env:
    VOSK_MODEL_DIR — absolute path to unzipped vosk model dir.
                     Default: <repo>/stt_experiments/voice_models/vosk-model-en-us-0.22
"""
import json
import os
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[1]
    / "voice_models"
    / "vosk-model-en-us-0.22"
)
_MODEL_DIR = Path(os.getenv("VOSK_MODEL_DIR", _DEFAULT_MODEL_DIR))
_model = None  # loaded on first transcribe() call, ~2GB RSS


def _get_model():
    global _model
    if _model is not None:
        return _model
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    if not _MODEL_DIR.is_dir():
        raise FileNotFoundError(
            f"Vosk model dir not found: {_MODEL_DIR}. "
            f"Set VOSK_MODEL_DIR or unzip a model there. "
            f"Expected dir with am/, conf/, graph/, ivector/ subfolders."
        )
    _model = Model(str(_MODEL_DIR))
    return _model


def transcribe(wav_path: str) -> str:
    from vosk import KaldiRecognizer
    model = _get_model()
    with wave.open(wav_path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(
                f"{wav_path}: need mono s16le, got "
                f"channels={wf.getnchannels()} sampwidth={wf.getsampwidth()}"
            )
        rec = KaldiRecognizer(model, wf.getframerate())
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            rec.AcceptWaveform(data)
        result = json.loads(rec.FinalResult())
    return result.get("text", "").strip()
