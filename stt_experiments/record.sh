#!/usr/bin/env bash
# Record PCM2902 USB mic → wav → clean with selected cleaners → STT.
#
# Usage:
#   ./stt_experiments/record.sh [seconds] [--clean a,b] [--stt x,y]
#   ./stt_experiments/record.sh 5
#   ./stt_experiments/record.sh 5 --clean all
#   ./stt_experiments/record.sh 6 --clean normalize,highpass --stt vosk_basic
#
# Env overrides: MIC_CARD=<n>

set -euo pipefail

SECS="${1:-5}"; shift || true
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT_DIR="$HERE/samples"
mkdir -p "$OUT_DIR"

if [[ -z "${MIC_CARD:-}" ]]; then
    MIC_CARD="$(arecord -l | awk '/^card [0-9]+:.*USB/ { gsub(":",""); print $2; exit }')"
    if [[ -z "$MIC_CARD" ]]; then
        echo "no USB mic found. arecord -l:" >&2; arecord -l >&2; exit 1
    fi
fi

DEV="plughw:${MIC_CARD},0"
TS="$(date +%Y%m%d_%H%M%S)"
WAV="$OUT_DIR/rec_${TS}.wav"

echo "mic=$DEV  secs=$SECS  out=$WAV"
echo "speak now..."
arecord -D "$DEV" -f S16_LE -r 16000 -c 1 -d "$SECS" "$WAV"
echo "recorded."

cd "$ROOT"
uv run python -m stt_experiments.run "$WAV" "$@"
