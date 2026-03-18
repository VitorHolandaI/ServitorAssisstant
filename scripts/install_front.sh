#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Servitor Assistant – FRONTEND install script
# Run this on the server machine (or any machine that serves the UI)
# ─────────────────────────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[install-front]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Servitor – Frontend Install${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Check Node / npm ──────────────────────────────────────────────

if ! command -v node &>/dev/null; then
    err "Node.js not found. Install it from https://nodejs.org (LTS recommended)"
fi

if ! command -v npm &>/dev/null; then
    err "npm not found. It should come with Node.js."
fi

ok "Node $(node --version) / npm $(npm --version)"

# ── Install dependencies ──────────────────────────────────────────

log "Installing frontend dependencies..."
cd "$ROOT_DIR/front"
npm install
ok "node_modules ready"

# ── Remind about .env ─────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[NOTE]${NC} Check front/.env and make sure the server IP is correct:"
echo "  VITE_REACT_APP_API_URL=http://<SERVER_IP>:8000/..."
echo "  Current value: $(grep VITE_REACT_APP_API_URL_STREAM "$ROOT_DIR/front/.env" | head -1)"
echo ""

# ── Done ──────────────────────────────────────────────────────────

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Frontend install complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  The frontend is started automatically by ${YELLOW}./start.sh${NC}"
echo "  To run it standalone:  ${YELLOW}cd front && npm run dev${NC}"
echo ""
