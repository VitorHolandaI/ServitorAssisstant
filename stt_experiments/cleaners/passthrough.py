"""No-op cleaner: re-encode to mono s16le, no processing. Baseline."""
from ._io import load_mono, write_mono_s16


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    write_mono_s16(out_wav, data, sr)
