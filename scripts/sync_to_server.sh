#!/bin/bash
# Sync the project to a remote server via rsync.
# Default destination keeps the same path under /home/vitor.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_REMOTE_PATH="/home/vitor/git/ServitorAssisstant"
DEFAULT_REMOTE_USER="vitor"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[sync]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

usage() {
    cat <<EOF
Usage:
  $(basename "$0") <server_ip> [--path REMOTE_PATH] [--user REMOTE_USER] [--dry-run]

Examples:
  $(basename "$0") 10.66.66.16
  $(basename "$0") 10.66.66.16 --path /home/vitor/git/ServitorAssisstant
  $(basename "$0") 10.66.66.16 --user vitor --path /srv/servitor

Notes:
  - Uses rsync over SSH.
  - Excludes local secrets, caches, logs, runtime data, and git metadata.
  - Default remote path: $DEFAULT_REMOTE_PATH
  - Default remote user: $DEFAULT_REMOTE_USER
EOF
}

if ! command -v rsync >/dev/null 2>&1; then
    err "rsync not found"
fi

if ! command -v ssh >/dev/null 2>&1; then
    err "ssh not found"
fi

SERVER_IP=""
REMOTE_PATH="$DEFAULT_REMOTE_PATH"
REMOTE_USER="$DEFAULT_REMOTE_USER"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --path)
            shift
            [[ $# -gt 0 ]] || err "--path requires a value"
            REMOTE_PATH="$1"
            ;;
        --user)
            shift
            [[ $# -gt 0 ]] || err "--user requires a value"
            REMOTE_USER="$1"
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            err "Unknown option: $1"
            ;;
        *)
            if [[ -n "$SERVER_IP" ]]; then
                err "Only one server_ip can be provided"
            fi
            SERVER_IP="$1"
            ;;
    esac
    shift
done

[[ -n "$SERVER_IP" ]] || { usage; exit 1; }

RSYNC_ARGS=(
    -az
    --checksum
    --delete
    --human-readable
    --info=progress2
    --itemize-changes
    --exclude=.git/
    --exclude=.gitignore
    --exclude=.env
    --exclude=.venv/
    --exclude=api/.env
    --exclude=front/.env
    --exclude=.claude/
    --exclude=.codex
    --exclude=.codex/
    --exclude=CLAUDE.md
    --exclude=logs/
    --exclude=data/
    --exclude=__pycache__/
    --exclude=*.pyc
    --exclude=node_modules/
    --exclude=front/node_modules/
    --exclude=front/dist/
    --exclude=front/dist-ssr/
    --exclude=*.pem
    --exclude=*.key
    --exclude=melhor.txt
    --exclude=voice_models/*.onnx
    --exclude=voice_models/*.onnx.json
    --exclude=voice_models/*.onnx.json.*
    --exclude=stt_experiments/samples/
    --exclude=stt_experiments/voice_models/
)

if [[ "$DRY_RUN" -eq 1 ]]; then
    RSYNC_ARGS+=(--dry-run)
    warn "Dry run enabled; no files will be changed remotely"
fi

REMOTE_TARGET="${REMOTE_USER}@${SERVER_IP}:${REMOTE_PATH}/"

log "Ensuring remote path exists: ${REMOTE_TARGET}"
ssh "${REMOTE_USER}@${SERVER_IP}" "mkdir -p '${REMOTE_PATH}'"

log "Syncing ${ROOT_DIR}/ -> ${REMOTE_TARGET}"
rsync "${RSYNC_ARGS[@]}" "${ROOT_DIR}/" "${REMOTE_TARGET}"

ok "Sync complete"
