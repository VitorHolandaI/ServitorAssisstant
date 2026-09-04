#!/usr/bin/env bash
# Fetch the models the wake-word listener needs. None of them are committed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
MODELS="$ROOT_DIR/voice_models"
mkdir -p "$MODELS"

VOSK_MODEL="${VOSK_MODEL:-vosk-model-small-en-us-0.15}"
if [ ! -d "$MODELS/$VOSK_MODEL" ]; then
    echo "[models] vosk: $VOSK_MODEL"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL -o "$tmp/vosk.zip" "https://alphacephei.com/vosk/models/${VOSK_MODEL}.zip"
    unzip -q "$tmp/vosk.zip" -d "$MODELS"
fi

for voice in en_US-ryan-medium pt_BR-faber-medium; do
    if [ ! -f "$MODELS/$voice.onnx" ]; then
        echo "[models] piper: $voice"
        "$PY" -m piper.download_voices "$voice" --data-dir "$MODELS"
    fi
done

echo "[models] openvino models"
"$PY" - <<'PYEOF'
from pathlib import Path
from huggingface_hub import snapshot_download

root = Path(__file__).resolve().parent if False else Path("voice_models")
for repo, name in [
    ("OpenVINO/whisper-base-fp16-ov", "whisper-base-fp16-ov"),
    ("OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov", "qwen2.5-1.5b-instruct-int4-ov"),
]:
    dest = root / name
    if dest.is_dir():
        print(f"[models] have {name}")
        continue
    print(f"[models] {repo}")
    snapshot_download(repo_id=repo, local_dir=str(dest))
PYEOF

echo "[models] done"
