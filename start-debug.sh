#!/bin/bash
# ─────────────────────────────────────────────
# Servitor Assistant – DEBUG startup script
# Runs all services in FOREGROUND with full logs printed to terminal.
# Use this to trace errors manually.
# ─────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

export DEBUG=true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

log()  { echo -e "${CYAN}[start-debug]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; }
dbg()  { echo -e "${MAGENTA}[DEBUG]${NC} $1"; }

cleanup() {
    echo ""
    log "Shutting down all services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    wait 2>/dev/null
    log "All services stopped."
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

check_cmd uv
check_cmd npm
check_cmd ollama

if ! ollama list &>/dev/null; then
    log "Ollama not running – starting it..."
    ollama serve &
    PIDS+=($!)
    sleep 2
fi

dbg "DEBUG=true — all Python logs printed to terminal"
echo ""

# ── 1. General MCP Server (port 8001) — logs to file + terminal ──

log "Starting general MCP server on :8001 ..."
(
    cd "$ROOT_DIR/api/mcp_module/stremable_http"
    uv run --project "$ROOT_DIR" python stream2.py 2>&1 | tee "$ROOT_DIR/logs/mcp.log" | sed "s/^/${CYAN}[MCP]${NC} /"
) &
MCP_PID=$!
PIDS+=($MCP_PID)
ok "General MCP server PID=$MCP_PID"

# ── 2. Weekly Activity MCP Server (port 8002) ────────────────────

log "Starting weekly activity MCP server on :8002 ..."
(
    cd "$ROOT_DIR/api/mcp_module/dev_activity"
    uv run --project "$ROOT_DIR" python stream.py 2>&1 | tee "$ROOT_DIR/logs/mcp-activity.log" | sed "s/^/${MAGENTA}[MCP-ACTIVITY]${NC} /"
) &
MCP_ACTIVITY_PID=$!
PIDS+=($MCP_ACTIVITY_PID)
ok "Weekly activity MCP server PID=$MCP_ACTIVITY_PID"

sleep 3

# ── 3. Server API (port 8000) — logs to file + terminal ──────────

log "Starting Server API on :8000 ..."
(
    cd "$ROOT_DIR/api"
    uv run --project "$ROOT_DIR" python ServerApi.py 2>&1 | tee "$ROOT_DIR/logs/api.log" | sed "s/^/${GREEN}[API]${NC} /"
) &
API_PID=$!
PIDS+=($API_PID)
ok "Server API PID=$API_PID"

# ── 4. Frontend (port 5173) — logs to file only ───────────────────

log "Starting frontend (logs → logs/front.log)..."
(
    cd "$ROOT_DIR/front"
    npm run dev -- --host 0.0.0.0
) > "$ROOT_DIR/logs/front.log" 2>&1 &
FRONT_PID=$!
PIDS+=($FRONT_PID)
ok "Frontend PID=$FRONT_PID"

echo ""
echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${MAGENTA}  Servitor Assistant — DEBUG MODE${NC}"
echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Frontend  →  ${YELLOW}http://localhost:5173${NC}"
echo -e "  API       →  ${YELLOW}http://localhost:8000${NC}"
echo -e "  MCP       →  ${YELLOW}http://localhost:8001/mcp${NC}"
echo -e "  MCP Dev   →  ${YELLOW}http://localhost:8002/mcp${NC}"
echo -e "  Logs dir  →  ${YELLOW}logs/${NC}"
echo ""
echo -e "  ${MAGENTA}Press Ctrl+C to stop all services${NC}"
echo ""

wait
