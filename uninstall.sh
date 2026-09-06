#!/bin/bash
#
# Removes little-green-dot: stops the launch agent, deletes the plist, the local
# virtual environment, and the log. Your config file is left alone unless you
# pass --all.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.github.mikegc-aws.little-green-dot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/little-green-dot.log"
VENV="$REPO/.venv"

REMOVE_CONFIG=false
[[ "${1:-}" == "--all" ]] && REMOVE_CONFIG=true

say() { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

step "Stopping the launch agent"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
say "stopped"

step "Removing files"
for target in "$PLIST" "$LOG"; do
  if [[ -e "$target" ]]; then
    rm -f "$target"
    say "removed $target"
  fi
done
if [[ -d "$VENV" ]]; then
  rm -rf "$VENV"
  say "removed $VENV"
fi

if $REMOVE_CONFIG; then
  CONFIG="${LITTLE_GREEN_DOT_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/little-green-dot/config.toml}"
  if [[ -f "$CONFIG" ]]; then
    rm -f "$CONFIG"
    say "removed $CONFIG"
  fi
else
  say "left your config file in place (use --all to delete it too)"
fi

cat <<EOF

Done. The dot is gone from your menu bar.

This directory is still here — delete it whenever you like.
EOF
