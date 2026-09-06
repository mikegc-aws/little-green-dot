#!/bin/bash
#
# Installs little-green-dot: builds a local virtual environment and registers a
# launch agent so the dot reappears after login.
#
# Everything it touches:
#   <this directory>/.venv
#   ~/Library/LaunchAgents/com.github.mikegc-aws.little-green-dot.plist
#   ~/Library/Logs/little-green-dot.log
#
# No sudo. Nothing is installed system-wide.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.github.mikegc-aws.little-green-dot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/little-green-dot.log"
VENV="$REPO/.venv"
BIN="$VENV/bin/little-green-dot"

say() { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# XML-escape a string so odd characters in a path cannot break the plist.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

step "Checking prerequisites"
[[ "$(uname -s)" == "Darwin" ]] || die "little-green-dot is macOS only."
say "macOS $(sw_vers -productVersion)"

if command -v aws >/dev/null 2>&1; then
  say "AWS CLI: $(command -v aws)"
else
  say "WARNING: no 'aws' on PATH. Install the AWS CLI, or set aws_cli_path in the config."
fi

step "Building the virtual environment in .venv"
if command -v uv >/dev/null 2>&1; then
  say "using uv"
  uv venv --allow-existing "$VENV" >/dev/null
  uv pip install --quiet --python "$VENV/bin/python" --editable "$REPO"
else
  command -v python3 >/dev/null 2>&1 || die "Need either uv or python3 (3.11+)."
  python3 - <<'PY' || die "Python 3.11 or newer is required."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
  say "using $(python3 --version)"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet --editable "$REPO"
fi
[[ -x "$BIN" ]] || die "Install finished but $BIN is missing."
say "installed $("$BIN" --version)"

step "Checking your AWS credentials once"
if "$BIN" --once; then
  say "Looks good — the dot will be green."
else
  say "Not logged in right now. That is fine; the dot will start red and turn"
  say "green on its own once you log in."
fi

step "Writing the launch agent"
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$(xml_escape "$LABEL")</string>

    <key>ProgramArguments</key>
    <array>
        <string>$(xml_escape "$BIN")</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$(xml_escape "$REPO")</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Restart if it crashes, but respect Quit from the menu. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>

    <key>StandardOutPath</key>
    <string>$(xml_escape "$LOG")</string>
    <key>StandardErrorPath</key>
    <string>$(xml_escape "$LOG")</string>

    <!-- launchd hands processes a minimal PATH; give it the usual places so the
         AWS CLI can be found. No credentials or secrets belong in this file. -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST_EOF
plutil -lint "$PLIST" >/dev/null || die "Generated plist is invalid: $PLIST"
say "$PLIST"

step "Starting it"
# Ignore failures here: bootout errors when it was never loaded.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
if ! launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then
  say "bootstrap unavailable, falling back to launchctl load"
  launchctl load -w "$PLIST"
fi
sleep 1
if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  say "running"
else
  say "WARNING: the agent did not come up. Check $LOG"
fi

cat <<EOF

Done. Look for the dot in your menu bar (top right).

  🟢 credentials active      🔴 not logged in
  🟡 expiring soon           ⚪ can't tell

  Config:     $("$BIN" --where)   (or use "Edit Config…" in the menu)
  Log:        $LOG
  Check:      $BIN --once
  Restart:    launchctl kickstart -k gui/$UID/$LABEL
  Remove:     $REPO/uninstall.sh
EOF
