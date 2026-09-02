#!/bin/bash
# Sync this checkout to the deployment host, then carry over the files that
# rsync deliberately leaves behind.
#
# sync_to_server.sh excludes .env, and the Home Assistant token and CA live
# outside the repository on purpose, so a plain rsync leaves the server unable
# to reach Home Assistant. They are copied here instead of being committed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVER_IP="10.66.66.16"
REMOTE_USER="vitor"
REMOTE_PATH="/home/vitor/git/ServitorAssisstant"
REMOTE="${REMOTE_USER}@${SERVER_IP}"

# Host-local files, kept out of the repository. Same path on both machines.
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/servitor"

DRY_RUN=0
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=1
done

"$SCRIPT_DIR/sync_to_server.sh" \
    "$SERVER_IP" \
    --user "$REMOTE_USER" \
    --path "$REMOTE_PATH" \
    "$@"

copy_secret() {
    local src="$1" dest="$2" mode="$3"
    if [[ ! -f "$src" ]]; then
        echo "[resync] skipped (not found): $src"
        return
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[resync] would copy $src -> ${REMOTE}:$dest (mode $mode)"
        return
    fi
    scp -q "$src" "${REMOTE}:$dest"
    ssh "$REMOTE" "chmod $mode '$dest'"
    echo "[resync] copied $src -> $dest (mode $mode)"
}

if [[ "$DRY_RUN" -eq 0 ]]; then
    ssh "$REMOTE" "mkdir -p '$CONFIG_DIR'"
fi

copy_secret "${ROOT_DIR}/.env"            "${REMOTE_PATH}/.env"          600
copy_secret "${CONFIG_DIR}/ha-token"      "${CONFIG_DIR}/ha-token"       600
copy_secret "${CONFIG_DIR}/ha-root-ca.crt" "${CONFIG_DIR}/ha-root-ca.crt" 644

echo "[resync] done. Restart the service to pick it up:"
echo "         ssh $REMOTE 'systemctl --user restart <servitor-unit>'"
