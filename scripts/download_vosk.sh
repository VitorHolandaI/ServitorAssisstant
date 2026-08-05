#!/usr/bin/env bash
# Download a Vosk model and unpack it where speech_recognition expects it
# (used by ServerApi /file_recorded -> recognize_vosk).
#
# Usage:
#   scripts/download_vosk.sh                 # default: vosk-model-small-pt-0.3
#   VOSK_MODEL=vosk-model-small-en-us-0.15 scripts/download_vosk.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [ ! -x "$PY" ]; then
    echo "[vosk] venv python not found: $PY"
    echo "[vosk] set PYTHON=/path/to/python if the venv is elsewhere"
    exit 1
fi

MODEL_NAME="${VOSK_MODEL:-vosk-model-small-pt-0.3}"

exec "$PY" - "$MODEL_NAME" <<'PYEOF'
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile

MODEL_NAME = sys.argv[1]
BASE_URL = "https://alphacephei.com/vosk/models"

try:
    import speech_recognition as sr

    target = pathlib.Path(sr.__file__).parent / "models" / "vosk"
except ImportError:
    print("[vosk] speech_recognition not found; using ./vosk-model as target")
    target = pathlib.Path.cwd() / "vosk-model"

model_ok = (target / "final.mdl").is_file() or (target / "am" / "final.mdl").is_file()
if model_ok:
    print(f"[vosk] model already present at {target} — nothing to do")
    sys.exit(0)

url = f"{BASE_URL}/{MODEL_NAME}.zip"
print(f"[vosk] downloading {url}")
print(f"[vosk] target: {target}")

tmp = pathlib.Path(tempfile.mkdtemp(prefix="vosk-dl-"))
try:
    zip_path = tmp / "model.zip"
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    inner = [p for p in tmp.iterdir() if p.is_dir() and p.name != "model.zip"]
    if len(inner) != 1:
        print(f"[vosk] unexpected extraction layout: {[p.name for p in inner]}")
        sys.exit(2)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(inner[0]), target)
    print(f"[vosk] installed at {target}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
PYEOF
