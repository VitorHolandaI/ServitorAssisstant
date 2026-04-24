"""Entry point: `python -m stt_experiments.tools.spectrogram_comparator`."""
from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path

import matplotlib

matplotlib.use("TkAgg")

from .app import SpectrogramComparatorApp


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUI to compare multiple wav spectrograms and play them on click.",
    )
    parser.add_argument("wavs", nargs="*", help="optional wav files to preload")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = tk.Tk()
    app = SpectrogramComparatorApp(root, [Path(w) for w in args.wavs])
    if not app.panels:
        app.add_files_dialog()
    root.mainloop()


if __name__ == "__main__":
    main()
