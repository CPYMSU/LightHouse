#!/bin/bash
set -Eeuo pipefail

REPO_URL="https://github.com/CPYMSU/LightHouse.git"
INSTALL_ROOT="${LIGHTHOUSE_HOME:-$HOME/.lighthouse}"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
LOG_DIR="$INSTALL_ROOT/logs"
CONFIG_FILE="$INSTALL_ROOT/config.json"
PLIST="$HOME/Library/LaunchAgents/com.cpym.su.lighthouse.plist"
LABEL="com.cpym.su.lighthouse"
CONTROL_SERVICE="com.cpym.su.lighthouse.control"
MODEL_SERVICE="com.cpym.su.lighthouse.model"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mLightHouse installer: %s\033[0m\n' "$*" >&2; exit 1; }
tty_read() {
  local prompt="$1" value
  printf "%s" "$prompt" > /dev/tty
  IFS= read -r value < /dev/tty
  printf "%s" "$value"
}
tty_secret() {
  local prompt="$1" value
  printf "%s" "$prompt" > /dev/tty
  stty -echo < /dev/tty
  IFS= read -r value < /dev/tty || true
  stty echo < /dev/tty
  printf "\n" > /dev/tty
  printf "%s" "$value"
}

[[ "$(uname -s)" == "Darwin" ]] || fail "this installer currently supports macOS only"
[[ -t 1 ]] || fail "run the installer from an interactive Terminal window"

say "Installing LightHouse OS — one product, one terminal, built-in intelligence"

if ! xcode-select -p >/dev/null 2>&1; then
  say "Installing Apple Command Line Tools"
  xcode-select --install >/dev/null 2>&1 || true
  while ! xcode-select -p >/dev/null 2>&1; do
    printf "Complete the Apple installer window; waiting...\r"
    sleep 5
  done
  printf "\n"
fi

if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
command -v brew >/dev/null 2>&1 || fail "Homebrew installation did not become available"

say "Installing Python 3.12, PostgreSQL 16 and Git"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list postgresql@16 >/dev/null 2>&1 || brew install postgresql@16
brew list git >/dev/null 2>&1 || brew install git

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
PG_BIN="$(brew --prefix postgresql@16)/bin"
BREW_PREFIX="$(brew --prefix)"
mkdir -p "$INSTALL_ROOT" "$LOG_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$INSTALL_ROOT"

say "Starting the private local PostgreSQL control plane"
brew services start postgresql@16 >/dev/null
for _ in {1..60}; do
  "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break
  sleep 1
done
"$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 >/dev/null 2>&1 || fail "PostgreSQL did not become ready"
if ! "$PG_BIN/psql" -h 127.0.0.1 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='lighthouse'" | grep -q 1; then
  "$PG_BIN/createdb" -h 127.0.0.1 lighthouse
fi
DATABASE_URL="postgresql://$(id -un)@127.0.0.1:5432/lighthouse"

say "Downloading LightHouse"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --prune origin main
  git -C "$APP_DIR" reset --hard origin/main
else
  rm -rf "$APP_DIR"
  git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR"
fi

say "Installing the complete LightHouse package"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install "$APP_DIR"

CONTROL_KEY="$("$PYTHON" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
security add-generic-password -U -a "$(id -un)" -s "$CONTROL_SERVICE" -w "$CONTROL_KEY" >/dev/null

MODEL_BASE_URL="$(tty_read 'Model API base URL (for example https://api.openai.com/v1): ')"
MODEL_NAME="$(tty_read 'Model name: ')"
MODEL_KEY="$(tty_secret 'Model API key (hidden): ')"
[[ -n "$MODEL_BASE_URL" && -n "$MODEL_NAME" && -n "$MODEL_KEY" ]] || fail "model API base URL, model name and API key are required"
security add-generic-password -U -a "$(id -un)" -s "$MODEL_SERVICE" -w "$MODEL_KEY" >/dev/null
unset MODEL_KEY CONTROL_KEY

"$PYTHON" - "$CONFIG_FILE" "$DATABASE_URL" "$MODEL_BASE_URL" "$MODEL_NAME" <<'PY'
import json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = {
    "url": "http://127.0.0.1:8787",
    "database_url": sys.argv[2],
    "model_base_url": sys.argv[3],
    "model": sys.argv[4],
    "model_json_mode": True,
    "actor": os.environ.get("USER") or "operator",
    "host": "127.0.0.1",
    "port": 8787,
}
path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$VENV_DIR/bin/lighthouse-api</string></array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>$VENV_DIR/bin:$PG_BIN:$BREW_PREFIX/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LIGHTHOUSE_CONFIG</key><string>$CONFIG_FILE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/server-error.log</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST
chmod 600 "$PLIST"
ln -sf "$VENV_DIR/bin/lh" "$BREW_PREFIX/bin/lh"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

say "Waiting for LightHouse"
for _ in {1..60}; do
  curl -fsS http://127.0.0.1:8787/healthz >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8787/healthz >/dev/null || fail "LightHouse did not become healthy; inspect $LOG_DIR/server-error.log"
"$BREW_PREFIX/bin/lh" migrate >/dev/null
"$BREW_PREFIX/bin/lh" doctor

say "LightHouse is installed."
printf "\nOpen any project and run:\n\n  cd /path/to/project\n  lh\n\n"
printf "LightHouse will bind that directory and start its built-in reasoning loop.\n"
