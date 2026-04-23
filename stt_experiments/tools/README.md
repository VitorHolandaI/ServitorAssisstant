# tools

Utilities for inspecting and comparing experiment outputs.

## Spectrogram Comparator

Open a desktop GUI that lets you load as many wav files as you want,
render one spectrogram per file, and play a clip by clicking its panel:

```bash
./.venv/bin/python -m stt_experiments.tools.spectrogram_comparator
```

You can also open files directly from the command line:

```bash
./.venv/bin/python -m stt_experiments.tools.spectrogram_comparator \
  stt_experiments/samples/rec_20260423_095413.wav \
  stt_experiments/samples/rec_20260423_095413__highpass.wav
```

Controls:

- `Add WAVs`: append more audio files without clearing the current list
- `Clear`: remove all loaded spectrograms and stop playback
- click a spectrogram panel: select it and start playback
- `Stop`: stop the currently playing clip
