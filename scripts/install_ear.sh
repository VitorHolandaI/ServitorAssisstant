#!/usr/bin/env bash
# Install the wake-word listener: the bar widget and the user service.
#
# The plugin is symlinked rather than copied so editing it in the checkout is
# what the shell actually loads — Omarchy reloads plugin code on save.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/vitor.servitor"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$(dirname "$PLUGIN_DIR")" "$UNIT_DIR"

if [ -e "$PLUGIN_DIR" ] && [ ! -L "$PLUGIN_DIR" ]; then
    echo "[ear] $PLUGIN_DIR exists and is not a symlink; leaving it alone" >&2
    exit 1
fi
ln -sfn "$ROOT_DIR/omarchy/vitor.servitor" "$PLUGIN_DIR"
echo "[ear] plugin linked: $PLUGIN_DIR -> $ROOT_DIR/omarchy/vitor.servitor"

sed "s|@ROOT_DIR@|$ROOT_DIR|g" "$ROOT_DIR/scripts/servitor-ear.service" > "$UNIT_DIR/servitor-ear.service"
systemctl --user daemon-reload
echo "[ear] unit installed: $UNIT_DIR/servitor-ear.service"

cat <<MSG

Next:
  systemctl --user enable --now servitor-ear
  $ROOT_DIR/scripts/servitor-ear status

Then add the widget to the bar by putting {"id": "vitor.servitor"} in the
right-hand layout of ~/.config/omarchy/shell.json (it hot-reloads on save).
MSG
