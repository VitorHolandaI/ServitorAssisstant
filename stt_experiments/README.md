# stt_experiments

Playground for improving speech-to-text. Pipeline:

```
wav → [cleaner] → <base>__<cleaner>.wav → [stt strategy] → text
```

Each cleaner writes its output next to the input so you can inspect/listen
to the cleaned audio and attribute any accuracy change to that exact step.

## Quick Start

Use the project virtualenv:

```bash
./.venv/bin/python -m stt_experiments.run stt_experiments/samples/<file>.wav --clean all
```

Record from the USB mic and run the default pipeline:

```bash
./stt_experiments/record.sh 5 --clean all
```

Run one cleaner at a time with the new single-stage programs:

```bash
./.venv/bin/python -m stt_experiments.stage_recorders.record_highpass 5
./.venv/bin/python -m stt_experiments.stage_recorders.record_spectral_gate 5
./.venv/bin/python -m stt_experiments.stage_recorders.record_normalize 5
```

Chain outputs manually:

```bash
./.venv/bin/python -m stt_experiments.stage_recorders.record_highpass 5
./.venv/bin/python -m stt_experiments.stage_recorders.record_spectral_gate \
  --input stt_experiments/stage_recorders/output/rec_YYYYMMDD_HHMMSS__highpass.wav
./.venv/bin/python -m stt_experiments.stage_recorders.record_normalize \
  --input stt_experiments/stage_recorders/output/rec_YYYYMMDD_HHMMSS__highpass__spectral_gate.wav
```

Outputs you want to keep in git:

- code under `stt_experiments/`

Outputs that stay local only:

- `stt_experiments/samples/`
- `stt_experiments/voice_models/`
- `stt_experiments/*.zip`
- `stt_experiments/stage_recorders/output/`

Open the spectrogram GUI comparator:

```bash
./.venv/bin/python -m stt_experiments.tools.spectrogram_comparator
```

## Layout

```
stt_experiments/
├── record.sh             # Record USB mic + run pipeline
├── run.py                # Run cleaners + STT on an existing wav
├── cleaners/             # wav → wav preprocessing
│   ├── passthrough.py    # baseline (re-encode only)
│   ├── normalize.py      # peak normalize to -1 dBFS
│   ├── highpass.py       # 80 Hz high-pass (rumble/hum)
│   ├── bandpass_voice.py # 300–3400 Hz (telephony band)
│   ├── preemphasis.py    # y[n]=x[n]-0.97*x[n-1]
│   ├── trim_silence.py   # librosa.effects.trim
│   └── spectral_gate.py  # spectral subtraction using first 0.3 s as noise
├── strategies/           # wav → text
│   └── vosk_basic.py     # Vosk direct, VOSK_MODEL_DIR env
├── tools/                # helper GUIs and local analysis tools
├── samples/              # recorded + cleaned wavs
└── voice_models/         # unzipped vosk model (gitignored)
```

## Mic

PCM2902 USB (`08bb:2902`). Auto-detected by `record.sh`. Override with
`MIC_CARD=<n>`. Capture forced to 16 kHz mono s16le (Vosk format).

## Usage

Record 5 s, run all cleaners + all STT strategies:
```bash
./stt_experiments/record.sh 5 --clean all
```

Subset:
```bash
./stt_experiments/record.sh 6 --clean normalize,highpass --stt vosk_basic
```

Replay on saved wav:
```bash
uv run python -m stt_experiments.run stt_experiments/samples/rec_*.wav --clean all
```

Output columns: `cleaner | stt | elapsed_sec | text`. Cleaned wavs land
at `samples/<base>__<cleaner>.wav` — open in Audacity/ffplay to inspect.

## Adding a cleaner

1. `cleaners/<name>.py` exposing `clean(in_wav: str, out_wav: str) -> None`.
   Use `cleaners._io.load_mono` / `write_mono_s16` for I/O.
2. Register in `cleaners/__init__.py`:
   ```python
   from .my_clean import clean as my_clean
   CLEANERS["my_clean"] = my_clean
   ```

## Adding an STT strategy

`strategies/<name>.py` with `transcribe(wav_path: str) -> str`. Register
in `strategies/__init__.py`. See `vosk_basic.py`.

## Ideas (not yet installed)

- `noisereduce` — stationary + non-stationary noise reduction.
- `webrtcvad` — true VAD, trim only voiced segments.
- `faster-whisper` — Whisper base/small for comparison vs Vosk.
- RNNoise via `rnnoise-python`.
