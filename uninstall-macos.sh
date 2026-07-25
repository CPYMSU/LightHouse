#!/bin/bash
set -Eeuo pipefail

INSTALL_ROOT="${LIGHTHOUSE_HOME:-$HOME/.lighthouse}"
PLIST="$HOME/Library/LaunchAgents/com.cpym.su.lighthouse.plist"
LABEL="com.cpym.su.lighthouse"
CONTROL_SERVICE="com.cpym.su.lighthouse.control"
MODEL_SERVICE="com.cpym.su.lighthouse.model"

[[ "$(uname -s)" == "Darwin" ]] || { echo "This uninstaller supports macOS only." >&2; exit 1; }

if [[ -x "$INSTALL_ROOT/venv/bin/python" ]]; then
  LIGHTHOUSE_HOME="$INSTALL_ROOT" "$INSTALL_ROOT/venv/bin/python" - <<'PY' || true
from lighthouse.instances import list_instances, stop_instance
for record in list_instances():
    if record.id != "default":
        try:
            stop_instance(record.id, force=True)
        except Exception:
            pass
PY
fi

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
rm -f "$PLIST"
security delete-generic-password -a "$(id -un)" -s "$CONTROL_SERVICE" >/dev/null 2>&1 || true
security delete-generic-password -a "$(id -un)" -s "$MODEL_SERVICE" >/dev/null 2>&1 || true
if command -v brew >/dev/null 2>&1; then
  rm -f "$(brew --prefix)/bin/lh"
fi
rm -rf "$INSTALL_ROOT"
echo "LightHouse application files, managed instances and Keychain credentials were removed."
echo "PostgreSQL 16 and its lighthouse database were kept to avoid deleting data."
