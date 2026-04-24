"""Pre-emphasis y[n] = x[n] - 0.97*x[n-1]. Boosts highs, standard
first step in many ASR front-ends."""
import numpy as np

from ._io import load_mono, write_mono_s16

_COEF = 0.97


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    emphasized = np.append(data[0], data[1:] - _COEF * data[:-1]).astype("float32")
    write_mono_s16(out_wav, emphasized, sr)
