"""Conditional gain: boost quiet signals to target RMS, leave loud
signals alone. Never attenuates.

Why RMS instead of peak: RMS tracks perceived loudness of sustained
speech. Peak-normalize reacts to isolated transients (claps, clicks)
and leaves actual voice still quiet.

Hard ceiling at -1 dBFS peak prevents clipping when boosting.
"""
import numpy as np

from ._io import load_mono, write_mono_s16

_TARGET_RMS_DBFS = -20.0  # typical spoken voice level
_PEAK_CEIL_DBFS = -1.0

_TARGET_RMS = 10 ** (_TARGET_RMS_DBFS / 20.0)
_PEAK_CEIL = 10 ** (_PEAK_CEIL_DBFS / 20.0)


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    rms = float(np.sqrt(np.mean(data ** 2)))
    peak = float(np.max(np.abs(data)))
    if rms <= 0 or peak <= 0:
        write_mono_s16(out_wav, data, sr)
        return

    if rms >= _TARGET_RMS:
        write_mono_s16(out_wav, data, sr)
        return

    gain = _TARGET_RMS / rms
    if peak * gain > _PEAK_CEIL:
        gain = _PEAK_CEIL / peak

    write_mono_s16(out_wav, (data * gain).astype("float32"), sr)
