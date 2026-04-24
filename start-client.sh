#!/bin/bash
# ─────────────────────────────────────────────
# Servitor Client – Raspberry Pi startup script
# Starts: Client API (8000) with LED control & speech recognition
# ─────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[start-client.sh]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

cleanup() {
    echo ""
    log "Shutting down client..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    wait 2>/dev/null
    log "Client stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# ── Ensure logs directory exists ─────────────────────────────────
mkdir -p "$ROOT_DIR/logs"

# ── Check required tools ──────────────────────────────────────────

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "Required command not found: $1"
        exit 1
    fi
}

check_cmd python3

# ── Check for Raspberry Pi GPIO support ───────────────────────────

if ! python3 -c "import RPi.GPIO" 2>/dev/null; then
    err "RPi.GPIO not available – ensure running on Raspberry Pi"
    exit 1
fi

# ── Client API (port 8000) ────────────────────────────────────────

CLIENT_PYTHON="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    CLIENT_PYTHON="$ROOT_DIR/.venv/bin/python"
fi

log "Starting Client API on :8000 ..."
(
    cd "$ROOT_DIR/api"
    "$CLIENT_PYTHON" ClientApi.py
) > "$ROOT_DIR/logs/client.log" 2>&1 &
CLIENT_PID=$!
PIDS+=($CLIENT_PID)
ok "Client API PID=$CLIENT_PID  (logs/client.log)"

# ── Summary ───────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Servitor Client is running${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Client API  →  ${YELLOW}http://localhost:8000${NC}"
echo ""
echo -e "  ${CYAN}Press Ctrl+C to stop${NC}"
echo ""

# Re-wait (cleanup runs on Ctrl+C)
wait
