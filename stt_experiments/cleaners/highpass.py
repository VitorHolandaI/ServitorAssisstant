"""High-pass filter at 80 Hz. Kills mains hum + rumble below speech."""
from scipy.signal import butter, sosfiltfilt

from ._io import load_mono, write_mono_s16


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    sos = butter(N=4, Wn=80.0, btype="highpass", fs=sr, output="sos")
    filtered = sosfiltfilt(sos, data).astype("float32")
    write_mono_s16(out_wav, filtered, sr)
