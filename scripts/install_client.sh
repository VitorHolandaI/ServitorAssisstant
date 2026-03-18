#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Servitor Assistant – CLIENT install script
# Run this on the Raspberry Pi
# ─────────────────────────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[install-client]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Servitor – Raspberry Pi Client Install${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── 1. System packages ────────────────────────────────────────────

log "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    portaudio19-dev \
    sox \
    libsox-fmt-all \
    ffmpeg \
    python3-pip \
    python3-dev \
    build-essential \
    libatlas-base-dev   # needed for numpy on Pi
ok "System packages installed"

# ── 2. Python packages ────────────────────────────────────────────

log "Installing Python packages..."
pip3 install --break-system-packages \
    fastapi \
    uvicorn \
    RPi.GPIO \
    sounddevice \
    soundfile \
    playsound3 \
    SpeechRecognition \
    requests \
    numpy \
    pyaudio \
    sox
ok "Python packages installed"

# ── Done ──────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Client install complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Run the client with:"
echo "    ${YELLOW}cd api && python3 ClientApi.py${NC}"
echo ""
echo "  Make sure the server IP in ClientApi.py matches your server:"
echo "    ${YELLOW}api/ClientApi.py${NC}  →  ServitorClient(\"ServitorClient\", \"<SERVER_IP>\", 12)"
echo ""
