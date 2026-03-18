#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Servitor Assistant – SERVER install script
# Run this once on the server machine (your main PC/laptop)
# ─────────────────────────────────────────────────────────────────

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[install-server]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Servitor – Server Install${NC}"
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
    python3-dev \
    build-essential
ok "System packages installed"

# ── 2. uv (Python package manager) ───────────────────────────────

if ! command -v uv &>/dev/null; then
    log "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this script
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    ok "uv installed"
else
    ok "uv already installed ($(uv --version))"
fi

# ── 3. Python dependencies (via uv) ──────────────────────────────

log "Installing Python dependencies from pyproject.toml..."
cd "$ROOT_DIR"
uv sync
ok "Python dependencies installed"

# ── 4. Ollama ─────────────────────────────────────────────────────

if ! command -v ollama &>/dev/null; then
    log "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed"
else
    ok "Ollama already installed"
fi

log "Pulling LLM model (llama3.2:1b) — this may take a while..."
ollama pull llama3.2:1b
ok "Model ready"

# ── 5. Vosk speech recognition model ─────────────────────────────

VOSK_MODEL_DIR="$ROOT_DIR/voice_models/vosk-model-small-en-us"
if [ ! -d "$VOSK_MODEL_DIR" ]; then
    log "Downloading Vosk speech recognition model..."
    VOSK_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    VOSK_ZIP="$ROOT_DIR/voice_models/vosk-model.zip"
    mkdir -p "$ROOT_DIR/voice_models"
    curl -L "$VOSK_URL" -o "$VOSK_ZIP"
    unzip -q "$VOSK_ZIP" -d "$ROOT_DIR/voice_models/"
    rm "$VOSK_ZIP"
    ok "Vosk model downloaded to $VOSK_MODEL_DIR"
else
    ok "Vosk model already present"
fi

# ── 6. Piper TTS voice model ──────────────────────────────────────

PIPER_MODEL="$ROOT_DIR/voice_models/en_US-ryan-medium.onnx"
if [ ! -f "$PIPER_MODEL" ]; then
    warn "Piper voice model not found at: $PIPER_MODEL"
    warn "Download it manually from: https://huggingface.co/rhasspy/piper-voices"
    warn "Place both .onnx and .onnx.json files in: $ROOT_DIR/voice_models/"
else
    ok "Piper voice model found"
fi

# ── 7. Create data directory ──────────────────────────────────────

mkdir -p "$ROOT_DIR/data"
ok "data/ directory ready (SQLite DB will be created here on first run)"

# ── Done ──────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Server install complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Run the server with:  ${YELLOW}./start.sh${NC}  (from project root)"
echo ""
