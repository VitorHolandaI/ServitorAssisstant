"""Bandpass 300–3400 Hz (telephony band). Strips everything outside
the fundamental speech range. Can help on noisy captures, hurts if
the mic already has limited response."""
from scipy.signal import butter, sosfiltfilt

from ._io import load_mono, write_mono_s16


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    nyq = sr / 2.0
    high = min(3400.0, nyq * 0.95)
    sos = butter(N=4, Wn=[300.0, high], btype="bandpass", fs=sr, output="sos")
    filtered = sosfiltfilt(sos, data).astype("float32")
    write_mono_s16(out_wav, filtered, sr)
