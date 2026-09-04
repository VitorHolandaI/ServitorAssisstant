# Wake-word listener

An always-on listener for the laptop. It waits for a phrase, acknowledges out
loud, records one command, transcribes it, answers it with a local model and
speaks the answer. Nothing leaves the machine.

The Raspberry Pi client in `api/client` streams everything it hears to the
server. That is acceptable for a dedicated box; it is not acceptable for a
laptop that sits in front of you all day. This is the alternative.

## Shape

```
microphone
    |
    v
Vosk, grammar restricted to the wake phrase        CPU, ~2% of one core
    |  "hey oracle"
    v
Piper speaks the acknowledgement                   CPU
    |
    v
record until you stop talking                      energy threshold
    |
    v
Whisper, multilingual                              NPU, fresh pipeline per turn
    |  text, in whatever language you spoke
    v
Qwen2.5 1.5B int4                                  NPU
    |
    v
Piper speaks the reply                             CPU
```

## Why the wake gate is Vosk and not Whisper

The gate runs continuously, so its cost is paid all day. A Vosk recognizer
restricted by grammar to two words decodes at a measured realtime factor of
0.018 — 30 seconds of audio in 0.55 s. Whisper is far more accurate and far
too expensive to leave running; it is used for the command, where accuracy
actually matters and the cost is paid once per turn.

Grammar mode can only spot words already in the model's lexicon. `servitor` is
in neither the English nor the Portuguese model, which is why the default
phrase is not that. Words verified present in the English model include
`oracle`, `athena`, `sentinel`, `overseer`, `warden`, `jarvis`, `friday`.

Phrase choice was measured, not guessed, against 14 distractor sentences:

| phrase | wakes | false fires |
|---|---|---|
| `wake oracle` | 4/4 | 2/14 |
| `athena` | 4/4 | 1/14 |
| `hey oracle` | 4/4 | **0/14** |

A bare common word such as `wake` fires on "let me wake up and check the
server logs". Two words, the second one rare, is what makes it quiet.

## Why Whisper's pipeline is rebuilt every turn

The Intel NPU carries a state bug: an inference whose prompt is a single token
returns the previous call's result unless it is the first inference on that
request. Whisper's language detection is exactly such an inference, so a
long-lived pipeline transcribes each utterance in the *previous* utterance's
language, silently.

Measured here on `whisper-base-fp16-ov`, alternating English and Portuguese:

| | one shared pipeline | rebuilt per utterance |
|---|---|---|
| English | correct | correct |
| Portuguese | *"and please give it a thumbs up."* | correct |
| English | answered in Portuguese | correct |

Rebuilding costs about 0.8 s once the compiled blob is cached, against 0.3 s to
transcribe. That is the price of being able to switch language freely, and it
keeps the work on the NPU, which draws far less power than the GPU.

Note that a `pip install` of `openvino-genai` ships **its own** OpenVINO
plugins inside the virtualenv, so a patched plugin in `/usr/lib/openvino/` does
not apply to this code. The per-utterance rebuild does not depend on that
patch, which is another reason to prefer it.

## Install

```bash
scripts/install_ear.sh
systemctl --user enable --now servitor-ear
scripts/servitor-ear status
```

Then add the widget to the bar by putting `{"id": "vitor.servitor"}` into the
right-hand layout of `~/.config/omarchy/shell.json`. It hot-reloads on save.
The widget is a symlink into this checkout, so editing it here is what the
shell loads.

Models are downloaded, not committed:

```bash
scripts/download_ear_models.sh
```

## Using it

Click the bar glyph to open or close the microphone; it shows what the daemon
is actually doing, never what it was asked to do.

| glyph | meaning |
|---|---|
| `○` | off, microphone closed |
| `◉` | listening for the phrase |
| `●` | awake, recording your command (pulses) |
| `◔` | thinking |
| `▶` | speaking |
| `!` | error, hover for the reason |

From a terminal:

```bash
scripts/servitor-ear status
scripts/servitor-ear toggle
scripts/servitor-ear stream    # what the widget reads
```

## Timings measured on this machine

| stage | device | cost |
|---|---|---|
| wake gate | CPU | 0.018 realtime |
| Whisper pipeline build | NPU | 3.9 s first, 0.8 s cached |
| Whisper transcribe | NPU | ~0.3 s |
| Qwen reply | NPU | 2.4 – 6.8 s |
| Qwen reply | GPU | 0.15 – 1.1 s |

The GPU is much faster but raised `CL_OUT_OF_RESOURCES` partway through a run
with 8 GB of desktop applications resident; the iGPU has no memory of its own.
Set `EAR_LLM_DEVICE=GPU` when the machine is quiet.
