# stage_recorders

Three single-stage programs for recording audio and producing one cleaned
wav per run:

- `record_highpass.py`
- `record_spectral_gate.py`
- `record_normalize.py`

Each program can do one of two things:

1. record a fresh clip from the USB microphone and write an output wav
2. accept `--input <wav>` so the output of one stage can be reused by another

## Quick usage

Record new audio and apply only `highpass`:

```bash
uv run python -m stt_experiments.stage_recorders.record_highpass 5
```

Record new audio and apply only `spectral_gate`:

```bash
uv run python -m stt_experiments.stage_recorders.record_spectral_gate 5
```

Record new audio and apply only `normalize`:

```bash
uv run python -m stt_experiments.stage_recorders.record_normalize 5
```

All outputs default to `stt_experiments/stage_recorders/output/`.

## Combining outputs

Use `--input` to feed the output of one stage into the next:

```bash
uv run python -m stt_experiments.stage_recorders.record_highpass 5
uv run python -m stt_experiments.stage_recorders.record_spectral_gate \
  --input stt_experiments/stage_recorders/output/rec_YYYYMMDD_HHMMSS__highpass.wav
uv run python -m stt_experiments.stage_recorders.record_normalize \
  --input stt_experiments/stage_recorders/output/rec_YYYYMMDD_HHMMSS__highpass__spectral_gate.wav
```

You can also set the exact destination:

```bash
uv run python -m stt_experiments.stage_recorders.record_normalize \
  --input input.wav \
  --output output__normalize.wav
```

## Notes

- Recording uses `arecord` with `16 kHz`, mono, `PCM_16`.
- USB mic detection follows the same rule as `record.sh`.
- `spectral_gate` works best if the first `0.3 s` of the clip is silence.
