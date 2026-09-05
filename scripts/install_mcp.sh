#!/usr/bin/env bash
# Install the local MCP servers as a user service.
#
# All of them run in one process: each is a FastMCP instance that would
# otherwise carry its own interpreter, and that floor measured 59 MB apiece.
# The addresses do not change - every server still answers on its own port.
#
# The templated unit is kept for running one server on its own while working
# on it: systemctl --user start servitor-mcp@mcp_module.youtube.stream
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
    mcp_module.youtube.stream              # :8007 what is new on followed channels
)

mkdir -p "$UNIT_DIR"
sed "s|@ROOT_DIR@|$ROOT_DIR|g" "$ROOT_DIR/scripts/servitor-mcp@.service" > "$UNIT_DIR/servitor-mcp@.service"
sed "s|@ROOT_DIR@|$ROOT_DIR|g" "$ROOT_DIR/scripts/servitor-mcp-host.service" > "$UNIT_DIR/servitor-mcp-host.service"
systemctl --user daemon-reload
echo "[mcp] units installed in $UNIT_DIR"

echo
echo "Next:"
echo "  systemctl --user enable --now servitor-mcp-host"
echo
echo "To work on one server alone, stop the host and run just that one:"
for module in "${MODULES[@]}"; do
    echo "  systemctl --user start 'servitor-mcp@${module}'"
done
