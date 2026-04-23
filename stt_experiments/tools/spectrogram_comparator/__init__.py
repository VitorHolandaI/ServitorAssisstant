"""Spectrogram comparator GUI package.

Submodules:
  palette        — color constants + layout sizes
  time_ruler     — TimeRuler widget (ticks + playhead)
  audio_player   — sounddevice OutputStream wrapper
  panel          — SpectrogramPanel (per wav)
  app            — SpectrogramComparatorApp (top-level Tk)
  __main__       — `python -m ...` entry point
"""
from .app import SpectrogramComparatorApp

__all__ = ["SpectrogramComparatorApp"]
