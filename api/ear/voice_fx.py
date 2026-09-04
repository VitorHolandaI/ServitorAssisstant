"""Vox-caster processing: turn a clean TTS voice into a Magos.

A tech-priest does not sound like a person, but it still has to be understood
across a room. Every stage here is therefore bounded: the band stays wide
enough for consonants, the ring modulator sits under the voice rather than on
top of it, and clipping is soft. Intensity is one dial so the whole chain can
be backed off at once.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal


@dataclass(frozen=True)
class VoxProfile:
    """One tech-priest voice setting. `intensity` scales the machine in it."""

    intensity: float = 0.6
    pitch_ratio: float = 0.92      # below 1.0 lowers the voice
    ring_hz: float = 55.0          # metallic buzz; low enough to read as timbre
    ring_depth: float = 0.30       # at intensity 1.0
    band_low: float = 180.0        # keep body; a comms band alone muffles it
    band_high: float = 5200.0      # keep sibilance, or consonants disappear
    drive: float = 2.2             # soft clip amount at intensity 1.0
    detune_cents: float = 12.0     # doubled layer, slightly off
    detune_mix: float = 0.28
    echo_ms: float = 46.0          # short metallic slap, not a cathedral
    echo_mix: float = 0.22


def _resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """Linear resample. Used for pitch, so quality beyond this is wasted."""
    if abs(ratio - 1.0) < 1e-6:
        return x
    n = max(1, round(len(x) / ratio))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def apply_vox(audio: np.ndarray, sample_rate: int, profile: VoxProfile | None = None) -> np.ndarray:
    """Run the chain. Input and output are float32 in roughly [-1, 1]."""
    p = profile or VoxProfile()
    k = float(np.clip(p.intensity, 0.0, 1.0))
    x = np.asarray(audio, dtype=np.float32)
    if x.size == 0:
        return x

    # 1. Lower the voice. The caller synthesizes slightly fast so that
    #    resampling down restores the original duration.
    x = _resample(x, p.pitch_ratio)

    # 2. A second, detuned copy. One machine speaking with almost-one voice is
    #    most of what makes a servitor sound synthetic rather than merely deep.
    #    The twin is fitted to the original rather than the other way round:
    #    trimming `x` to the twin would shorten every reply, including at
    #    intensity zero where this stage is supposed to do nothing at all.
    if p.detune_mix * k > 0:
        twin = _resample(x, 2.0 ** (p.detune_cents / 1200.0))
        if len(twin) < len(x):
            twin = np.pad(twin, (0, len(x) - len(twin)))
        x = x + (p.detune_mix * k) * twin[: len(x)]

    # 3. Ring modulation, kept as an undertone. Full-depth ring modulation is
    #    the classic robot sound and is also unintelligible.
    t = np.arange(len(x), dtype=np.float32) / sample_rate
    x = x * (1.0 - p.ring_depth * k + p.ring_depth * k * np.sin(2 * np.pi * p.ring_hz * t))

    # 4. Vox-caster band. Wide enough to keep consonants readable.
    nyq = sample_rate / 2.0
    low = max(20.0, p.band_low) / nyq
    high = min(p.band_high, nyq * 0.98) / nyq
    b, a = signal.butter(2, [low, high], btype="band")
    x = signal.lfilter(b, a, x).astype(np.float32)

    # 5. Soft clip. tanh keeps the peaks bounded instead of shattering them.
    drive = 1.0 + (p.drive - 1.0) * k
    x = np.tanh(drive * x) / np.tanh(drive)

    # 6. Short slap echo: the inside of a machine, not a hall.
    if p.echo_mix > 0:
        delay = int(sample_rate * p.echo_ms / 1000.0)
        if 0 < delay < len(x):
            echoed = np.zeros_like(x)
            echoed[delay:] = x[:-delay]
            x = x + (p.echo_mix * k) * echoed

    peak = float(np.abs(x).max())
    if peak > 0:
        x = 0.92 * x / peak
    return x.astype(np.float32)


# Named settings, least to most machine. `heavy` is the edge of intelligible.
PROFILES = {
    "off": VoxProfile(intensity=0.0, pitch_ratio=1.0),
    "subtle": VoxProfile(intensity=0.35, pitch_ratio=0.96),
    "magos": VoxProfile(intensity=0.60, pitch_ratio=0.92),
    "heavy": VoxProfile(intensity=0.85, pitch_ratio=0.88),
}
