"""Shared wav I/O for cleaners. Always mono s16le — Vosk's format."""
import numpy as np
import soundfile as sf


def load_mono(path: str) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim == 2:
        data = data.mean(axis=1)
    return data, sr


def write_mono_s16(path: str, data: np.ndarray, sr: int) -> None:
    clipped = np.clip(data, -1.0, 1.0)
    sf.write(path, clipped, sr, subtype="PCM_16")
