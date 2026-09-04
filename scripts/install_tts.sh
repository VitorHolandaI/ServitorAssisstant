#!/usr/bin/env bash
# Install the Kokoro voice in its own virtualenv.
#
# kokoro-onnx needs numpy 2.x. This project pins numpy 1.26.4, and gruut —
# Piper's phonemizer — caps numpy below 2.0, so they cannot share an
# interpreter. Keeping Kokoro in .venv-tts is what lets us have both; the ear
# drives it as a subprocess (api/ear/speak.py).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-tts"
MODELS="$ROOT_DIR/voice_models"
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0

if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV" --python 3.11
    uv pip install --python "$VENV/bin/python" kokoro-onnx soundfile
else
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet kokoro-onnx soundfile
fi

mkdir -p "$MODELS"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [ ! -f "$MODELS/$f" ]; then
        echo "[tts] downloading $f"
        curl -fsSL --retry 2 -o "$MODELS/$f" "$BASE/$f"
    fi
done

echo "[tts] done. The ear picks Kokoro up automatically; restart it with:"
echo "      systemctl --user restart servitor-ear"
