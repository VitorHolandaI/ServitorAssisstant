"""Spectral subtraction noise gate.

Estimates noise profile from first 0.3 s (assumed silence), subtracts
its magnitude spectrum from the rest. Poor man's noisereduce — no new
dep needed. Works best when recording starts with silence.
"""
import numpy as np

from ._io import load_mono, write_mono_s16

_NOISE_SEC = 0.3
_OVERSUB = 1.5  # oversubtraction factor
_FLOOR = 0.02   # min spectral floor to avoid musical noise
_N_FFT = 1024
_HOP = 256


def _stft(x: np.ndarray) -> np.ndarray:
    win = np.hanning(_N_FFT).astype("float32")
    n_frames = 1 + max(0, (len(x) - _N_FFT) // _HOP)
    frames = np.stack(
        [x[i * _HOP : i * _HOP + _N_FFT] * win for i in range(n_frames)]
    )
    return np.fft.rfft(frames, axis=1)


def _istft(spec: np.ndarray, length: int) -> np.ndarray:
    win = np.hanning(_N_FFT).astype("float32")
    frames = np.fft.irfft(spec, axis=1).astype("float32") * win
    out = np.zeros(length + _N_FFT, dtype="float32")
    norm = np.zeros_like(out)
    for i, frame in enumerate(frames):
        s = i * _HOP
        out[s : s + _N_FFT] += frame
        norm[s : s + _N_FFT] += win * win
    norm[norm < 1e-8] = 1.0
    return (out / norm)[:length]


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    noise_n = int(_NOISE_SEC * sr)
    if len(data) <= noise_n + _N_FFT:
        write_mono_s16(out_wav, data, sr)
        return

    noise_spec = _stft(data[:noise_n])
    noise_mag = np.mean(np.abs(noise_spec), axis=0)

    full = _stft(data)
    mag = np.abs(full)
    phase = np.angle(full)
    cleaned_mag = np.maximum(mag - _OVERSUB * noise_mag, _FLOOR * mag)
    cleaned = cleaned_mag * np.exp(1j * phase)
    out = _istft(cleaned, len(data)).astype("float32")
    write_mono_s16(out_wav, out, sr)
