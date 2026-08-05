#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/sync_to_server.sh" \
    10.66.66.16 \
    --user vitor \
    --path /home/vitor/git/ServitorAssisstant \
    "$@"
