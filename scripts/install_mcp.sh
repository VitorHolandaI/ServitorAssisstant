#!/usr/bin/env bash
# Install the three local MCP servers as user services.
#
# One templated unit, instantiated per module, because the only thing that
# differs between them is the module path. The instance name IS the module,
# so `systemctl --user status servitor-mcp@mcp_module.profiles` reads as what
# it runs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

MODULES=(
    mcp_module.stremable_http.stream2      # :8001 general
    mcp_module.dev_activity.stream         # :8002 dev activity
    mcp_module.nextcloud_slim.stream       # :8003 nextcloud, slim
    mcp_module.desktop.stream              # :8004 dictation into the focused window
    mcp_module.browser.stream              # :8005 open sites and YouTube searches
    mcp_module.media.stream                # :8006 playback control over MPRIS
)

mkdir -p "$UNIT_DIR"
sed "s|@ROOT_DIR@|$ROOT_DIR|g" "$ROOT_DIR/scripts/servitor-mcp@.service" > "$UNIT_DIR/servitor-mcp@.service"
systemctl --user daemon-reload
echo "[mcp] unit installed: $UNIT_DIR/servitor-mcp@.service"

echo
echo "Next:"
for module in "${MODULES[@]}"; do
    echo "  systemctl --user enable --now 'servitor-mcp@${module}'"
done
