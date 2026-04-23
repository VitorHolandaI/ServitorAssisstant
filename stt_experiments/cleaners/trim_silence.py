"""Trim leading/trailing silence via librosa. Reduces noise padding
before/after speech, which Vosk sometimes hallucinates on."""
import librosa

from ._io import load_mono, write_mono_s16

_TOP_DB = 30.0  # everything below peak-30dB counts as silence


def clean(in_wav: str, out_wav: str) -> None:
    data, sr = load_mono(in_wav)
    trimmed, _ = librosa.effects.trim(data, top_db=_TOP_DB)
    write_mono_s16(out_wav, trimmed, sr)
