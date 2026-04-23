"""Hard-gated spectral subtraction.

Difference from spectral_gate: after subtracting the noise profile we
do NOT keep a soft floor. Bins whose magnitude is below
`_GATE_RATIO * noise_mag` are set to exactly zero. Voice bins survive
intact.

Result: silence between words is truly silent (no residual "shhhh"),
and we can boost afterwards without amplifying background.

Pipeline:
  1. Estimate noise profile from first 0.3 s.
  2. STFT of whole signal.
  3. Per bin: if mag < ratio * noise → 0. Else mag - noise (oversub).
  4. iSTFT with original phase.
  5. Peak-aware boost so surviving voice reaches target RMS, no clip.

Assumes first 0.3 s is silence. If you apply trim_silence first, the
noise profile will be contaminated with voice — run this BEFORE trim.
"""
import numpy as np

from ._io import load_mono, write_mono_s16

_NOISE_SEC = 0.3
_OVERSUB = 1.5
_GATE_RATIO = 2.5     # bin must be >= 2.5x noise (~8 dB SNR) to survive
_N_FFT = 1024
_HOP = 256

_TARGET_RMS_DBFS = -20.0
_PEAK_CEIL_DBFS = -1.0
_TARGET_RMS = 10 ** (_TARGET_RMS_DBFS / 20.0)
_PEAK_CEIL = 10 ** (_PEAK_CEIL_DBFS / 20.0)


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


def _boost_to_rms(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)))
    peak = float(np.max(np.abs(x)))
    if rms <= 0 or peak <= 0:
        return x
    if rms >= _TARGET_RMS:
        return x
    gain = _TARGET_RMS / rms
    if peak * gain > _PEAK_CEIL:
        gain = _PEAK_CEIL / peak
    return (x * gain).astype("float32")


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

    # hard gate: bins too close to noise floor → zero
    gate_mask = mag >= (_GATE_RATIO * noise_mag)
    cleaned_mag = np.where(gate_mask, mag - _OVERSUB * noise_mag, 0.0)
    cleaned_mag = np.maximum(cleaned_mag, 0.0)

    cleaned = cleaned_mag * np.exp(1j * phase)
    out = _istft(cleaned, len(data)).astype("float32")
    out = _boost_to_rms(out)
    write_mono_s16(out_wav, out, sr)
