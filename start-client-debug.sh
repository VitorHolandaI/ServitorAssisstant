#!/bin/bash
# ─────────────────────────────────────────────
# Servitor Client – DEBUG startup script
# Runs the Raspberry Pi client in foreground with logs in terminal.
# ─────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'
MAGENTA=$'\033[0;35m'
NC=$'\033[0m'

log() { echo -e "${CYAN}[start-client-debug]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
dbg() { echo -e "${MAGENTA}[DEBUG]${NC} $1"; }

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

mkdir -p "$ROOT_DIR/logs"

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "Required command not found: $1"
        exit 1
    fi
}

check_cmd python3
check_cmd tee
check_cmd sed

if ! python3 -c "import RPi.GPIO" 2>/dev/null; then
    err "RPi.GPIO not available – ensure running on Raspberry Pi"
    exit 1
fi

CLIENT_PYTHON="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    CLIENT_PYTHON="$ROOT_DIR/.venv/bin/python"
fi

dbg "Client logs will be printed to terminal and saved to logs/client.log"
echo ""

log "Starting Client API on :8000 ..."
(
    cd "$ROOT_DIR/api"
    "$CLIENT_PYTHON" ClientApi.py 2>&1 | tee "$ROOT_DIR/logs/client.log" | sed "s/^/${MAGENTA}[CLIENT]${NC} /"
) &
CLIENT_PID=$!
PIDS+=($CLIENT_PID)
ok "Client API PID=$CLIENT_PID"

echo ""
echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${MAGENTA}  Servitor Client — DEBUG MODE${NC}"
echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Client API  →  ${YELLOW}http://localhost:8000${NC}"
echo -e "  Log file    →  ${YELLOW}logs/client.log${NC}"
echo ""
echo -e "  ${MAGENTA}Press Ctrl+C to stop the client${NC}"
echo ""

wait
