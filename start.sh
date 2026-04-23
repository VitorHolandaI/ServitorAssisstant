#!/bin/bash
# ─────────────────────────────────────────────
# Servitor Assistant – Local startup script
# Starts: MCP servers (8001, 8002) + Server API (8000) + Frontend (5173)
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

log() { echo -e "${CYAN}[start.sh]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

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

# ── Ensure logs directory exists ─────────────────────────────────
mkdir -p "$ROOT_DIR/logs"

# ── Check required tools ──────────────────────────────────────────

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "Required command not found: $1"
        exit 1
    fi
}

check_cmd uv
check_cmd npm
check_cmd ollama

# ── Check ollama is running ───────────────────────────────────────

if ! ollama list &>/dev/null; then
    log "Ollama not running – starting it in background..."
    ollama serve &>/dev/null &
    PIDS+=($!)
    sleep 2
fi

# ── 1. General MCP Server (port 8001) ─────────────────────────────

log "Starting general MCP server on :8001 ..."
(
    cd "$ROOT_DIR/api/mcp_module/stremable_http"
    uv run --project "$ROOT_DIR" python stream2.py
) > "$ROOT_DIR/logs/mcp.log" 2>&1 &
MCP_PID=$!
PIDS+=($MCP_PID)
ok "General MCP server PID=$MCP_PID  (logs/mcp.log)"

# ── 2. Weekly Activity MCP Server (port 8002) ────────────────────

log "Starting weekly activity MCP server on :8002 ..."
(
    cd "$ROOT_DIR/api/mcp_module/dev_activity"
    uv run --project "$ROOT_DIR" python stream.py
) > "$ROOT_DIR/logs/mcp-activity.log" 2>&1 &
MCP_ACTIVITY_PID=$!
PIDS+=($MCP_ACTIVITY_PID)
ok "Weekly activity MCP server PID=$MCP_ACTIVITY_PID  (logs/mcp-activity.log)"

# Wait briefly for MCP to be ready before starting the API
sleep 2

# ── 3. Server API (port 8000) ─────────────────────────────────────

log "Starting Server API on :8000 ..."
(
    cd "$ROOT_DIR/api"
    uv run --project "$ROOT_DIR" python ServerApi.py
) > "$ROOT_DIR/logs/api.log" 2>&1 &
API_PID=$!
PIDS+=($API_PID)
ok "Server API PID=$API_PID  (logs/api.log)"

# ── 4. Frontend (port 5173) ───────────────────────────────────────

log "Starting frontend..."
(
    cd "$ROOT_DIR/front"
    npm run dev -- --host 0.0.0.0
) > "$ROOT_DIR/logs/front.log" 2>&1 &
FRONT_PID=$!
PIDS+=($FRONT_PID)
ok "Frontend PID=$FRONT_PID  (logs/front.log)"

# ── Summary ───────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Servitor Assistant is running${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Frontend  →  ${YELLOW}http://localhost:5173${NC}"
echo -e "  API       →  ${YELLOW}http://localhost:8000${NC}"
echo -e "  MCP       →  ${YELLOW}http://localhost:8001/mcp${NC}"
echo -e "  MCP Dev   →  ${YELLOW}http://localhost:8002/mcp${NC}"
echo ""
echo -e "  ${CYAN}Press Ctrl+C to stop all services${NC}"
echo ""

# Re-wait (cleanup runs on Ctrl+C)
wait
