"""Peak-normalize to -1 dBFS. Fixes quiet captures from PCM2902."""
import numpy as np

from ._io import load_mono, write_mono_s16

_TARGET = 10 ** (-1.0 / 20.0)  # -1 dBFS


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    peak = float(np.max(np.abs(data)))
    if peak > 0:
        data = data * (_TARGET / peak)
    write_mono_s16(out_wav, data, sr)
