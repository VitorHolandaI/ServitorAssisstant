#!/usr/bin/env bash
# Launcher for interactive menu.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec uv run python -m stt_experiments.menu
