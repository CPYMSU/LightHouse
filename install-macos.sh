#!/bin/bash
set -Eeuo pipefail

REPO_URL="https://github.com/CPYMSU/LightHouse.git"
RAW_INSTALL_URL="https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh"
API_INSTALL_URL="https://api.github.com/repos/CPYMSU/LightHouse/contents/install-macos.sh?ref=main"
INSTALL_ROOT="${LIGHTHOUSE_HOME:-$HOME/.lighthouse}"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
LOG_DIR="$INSTALL_ROOT/logs"
CONFIG_FILE="$INSTALL_ROOT/config.json"
PLIST="$HOME/Library/LaunchAgents/com.cpym.su.lighthouse.plist"
LABEL="com.cpym.su.lighthouse"
CONTROL_SERVICE="com.cpym.su.lighthouse.control"
MODEL_SERVICE="com.cpym.su.lighthouse.model"
PORT_START=8787

fetch_script() {
  local destination="$1" raw_url="$2" api_url="$3" source attempt
  for source in raw api; do
    for attempt in 1 2 3; do
      rm -f "$destination"
      if [[ "$source" == "raw" ]]; then
        curl --http1.1 --connect-timeout 20 --max-time 180 \
          --fail --location --silent --show-error \
          "$raw_url" -o "$destination" >/dev/null 2>&1 || true
      else
        curl --http1.1 --connect-timeout 20 --max-time 180 \
          --fail --location --silent --show-error \
          -H 'Accept: application/vnd.github.raw+json' \
          "$api_url" -o "$destination" >/dev/null 2>&1 || true
      fi
      if [[ -s "$destination" ]] \
        && head -n 1 "$destination" | grep -q '^#!/bin/bash' \
        && /bin/bash -n "$destination" >/dev/null 2>&1; then
        return 0
      fi
      sleep "$attempt"
    done
  done
  rm -f "$destination"
  return 1
}

if [[ ! -t 0 && "${LIGHTHOUSE_INSTALL_FROM_FILE:-0}" != "1" ]]; then
  BOOTSTRAP_FILE="$(mktemp "${TMPDIR:-/tmp}/lighthouse-install.XXXXXX")"
  if ! fetch_script "$BOOTSTRAP_FILE" "$RAW_INSTALL_URL" "$API_INSTALL_URL"; then
    printf 'LightHouse installer: could not download the installer from GitHub after automatic retries.\n' >&2
    rm -f "$BOOTSTRAP_FILE"
    exit 1
  fi
  chmod 700 "$BOOTSTRAP_FILE"
  export LIGHTHOUSE_INSTALL_FROM_FILE=1
  export LIGHTHOUSE_BOOTSTRAP_FILE="$BOOTSTRAP_FILE"
  exec /bin/bash "$BOOTSTRAP_FILE" </dev/tty
fi
if [[ -n "${LIGHTHOUSE_BOOTSTRAP_FILE:-}" ]]; then
  trap 'rm -f "$LIGHTHOUSE_BOOTSTRAP_FILE"' EXIT
fi

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mLightHouse installer: %s\033[0m\n' "$*" >&2; exit 1; }
show_server_logs() {
  local file="$LOG_DIR/server-error.log"
  if [[ -s "$file" ]]; then
    printf '\n--- LightHouse server error log ---\n' >&2
    tail -n 100 "$file" >&2 || true
    printf '%s\n\n' '--- end server error log ---' >&2
  fi
}
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

say "Installing LightHouse OS — multi-instance AI operating terminal"

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
  HOMEBREW_FILE="$(mktemp "${TMPDIR:-/tmp}/homebrew-install.XXXXXX")"
  if ! fetch_script \
    "$HOMEBREW_FILE" \
    "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh" \
    "https://api.github.com/repos/Homebrew/install/contents/install.sh?ref=HEAD"; then
    fail "Homebrew installer could not be downloaded from GitHub"
  fi
  /bin/bash "$HOMEBREW_FILE" </dev/tty
  rm -f "$HOMEBREW_FILE"
fi
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
command -v brew >/dev/null 2>&1 || fail "Homebrew installation did not become available"

say "Installing Python 3.12, PostgreSQL 16 and Git"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12 </dev/null
brew list postgresql@16 >/dev/null 2>&1 || brew install postgresql@16 </dev/null
brew list git >/dev/null 2>&1 || brew install git </dev/null

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
PG_BIN="$(brew --prefix postgresql@16)/bin"
BREW_PREFIX="$(brew --prefix)"
PG_DATA="$BREW_PREFIX/var/postgresql@16"
PG_LOG="$LOG_DIR/postgresql.log"
mkdir -p "$INSTALL_ROOT" "$LOG_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$INSTALL_ROOT"

postgres_ready() {
  "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 >/dev/null 2>&1
}

wait_for_postgres() {
  local attempts="${1:-30}" attempt
  for ((attempt = 0; attempt < attempts; attempt++)); do
    postgres_ready && return 0
    sleep 1
  done
  return 1
}

start_postgres_with_recovery() {
  if postgres_ready; then
    say "PostgreSQL is already ready"
    return 0
  fi

  if brew services start postgresql@16 >/dev/null 2>&1 </dev/null; then
    wait_for_postgres 30 && return 0
  fi

  say "PostgreSQL service startup issue detected; repairing automatically"
  brew services stop postgresql@16 >/dev/null 2>&1 </dev/null || true
  launchctl bootout "gui/$(id -u)/homebrew.mxcl.postgresql@16" >/dev/null 2>&1 || true
  brew services cleanup >/dev/null 2>&1 || true

  if brew services start postgresql@16 >/dev/null 2>&1 </dev/null; then
    wait_for_postgres 30 && return 0
  fi

  if [[ -f "$PG_DATA/PG_VERSION" ]]; then
    say "Homebrew service registration is unavailable; starting PostgreSQL directly"
    "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" start >/dev/null 2>&1 || true
    wait_for_postgres 30 && return 0
  fi

  return 1
}

SHELLENV_LINE="eval \"\$($BREW_PREFIX/bin/brew shellenv)\""
touch "$HOME/.zprofile"
if ! grep -Fqx "$SHELLENV_LINE" "$HOME/.zprofile"; then
  printf '\n%s\n' "$SHELLENV_LINE" >> "$HOME/.zprofile"
fi

say "Starting the shared local PostgreSQL Data and Memory Kernel"
if ! start_postgres_with_recovery; then
  printf '\nPostgreSQL could not be started automatically. Existing database files were not modified.\n' >&2
  printf 'Diagnostic log: %s\n' "$PG_LOG" >&2
  [[ -s "$PG_LOG" ]] && tail -n 80 "$PG_LOG" >&2 || true
  fail "PostgreSQL did not become ready"
fi
say "PostgreSQL is ready"
if ! "$PG_BIN/psql" -h 127.0.0.1 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='lighthouse'" | grep -q 1; then
  "$PG_BIN/createdb" -h 127.0.0.1 lighthouse
fi
DATABASE_URL="postgresql://$(id -un)@127.0.0.1:5432/lighthouse"

say "Downloading LightHouse"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --prune origin main </dev/null
  git -C "$APP_DIR" reset --hard origin/main
else
  rm -rf "$APP_DIR"
  GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR" </dev/null
fi

say "Installing the complete LightHouse package"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip </dev/null
"$VENV_DIR/bin/pip" install --upgrade "$APP_DIR" </dev/null

if ! security find-generic-password -a "$(id -un)" -s "$CONTROL_SERVICE" -w >/dev/null 2>&1; then
  CONTROL_KEY="$("$PYTHON" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
  )"
  security add-generic-password -U -a "$(id -un)" -s "$CONTROL_SERVICE" -w "$CONTROL_KEY" >/dev/null
  unset CONTROL_KEY
fi

EXISTING_MODEL_BASE="$("$PYTHON" - "$CONFIG_FILE" <<'PY'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
except Exception:
    value = {}
print(value.get('model_base_url') or '')
PY
)"
EXISTING_MODEL_NAME="$("$PYTHON" - "$CONFIG_FILE" <<'PY'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
except Exception:
    value = {}
print(value.get('model') or '')
PY
)"
MODEL_BASE_URL="${LIGHTHOUSE_MODEL_BASE_URL:-$EXISTING_MODEL_BASE}"
MODEL_NAME="${LIGHTHOUSE_MODEL:-$EXISTING_MODEL_NAME}"
if [[ -z "$MODEL_BASE_URL" ]]; then MODEL_BASE_URL="$(tty_read 'Model API base URL (for example https://api.openai.com/v1): ')"; fi
if [[ -z "$MODEL_NAME" ]]; then MODEL_NAME="$(tty_read 'Model name: ')"; fi
if ! security find-generic-password -a "$(id -un)" -s "$MODEL_SERVICE" -w >/dev/null 2>&1; then
  MODEL_KEY="${LIGHTHOUSE_MODEL_API_KEY:-}"
  if [[ -z "$MODEL_KEY" ]]; then MODEL_KEY="$(tty_secret 'Model API key (hidden): ')"; fi
  [[ -n "$MODEL_KEY" ]] || fail "model API key is required"
  security add-generic-password -U -a "$(id -un)" -s "$MODEL_SERVICE" -w "$MODEL_KEY" >/dev/null
  unset MODEL_KEY
fi
[[ -n "$MODEL_BASE_URL" && -n "$MODEL_NAME" ]] || fail "model API base URL and model name are required"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
for _ in {1..20}; do
  CURRENT_PORT="$("$PYTHON" - "$CONFIG_FILE" "$PORT_START" <<'PY'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
except Exception:
    value = {}
print(int(value.get('port') or sys.argv[2]))
PY
)"
  /usr/sbin/lsof -nP -iTCP:"$CURRENT_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.25
done
PREFERRED_PORT="$("$PYTHON" - "$CONFIG_FILE" "$PORT_START" <<'PY'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
except Exception:
    value = {}
print(int(value.get('port') or sys.argv[2]))
PY
)"
PORT="$("$PYTHON" - "$PREFERRED_PORT" <<'PY'
import socket, sys
start = max(1, int(sys.argv[1]))
for port in list(range(start, 65536)) + list(range(1024, start)):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
    except OSError:
        sock.close()
        continue
    sock.close()
    print(port)
    raise SystemExit(0)
raise SystemExit('no free local port is available')
PY
)"
if [[ "$PORT" != "$PREFERRED_PORT" ]]; then
  say "Port $PREFERRED_PORT is occupied; assigning the default LightHouse instance to $PORT"
fi

"$PYTHON" - "$CONFIG_FILE" "$DATABASE_URL" "$MODEL_BASE_URL" "$MODEL_NAME" "$PORT" <<'PY'
import json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding='utf-8'))
except Exception:
    value = {}
port = int(sys.argv[5])
value.update({
    'url': f'http://127.0.0.1:{port}',
    'database_url': sys.argv[2],
    'model_base_url': sys.argv[3].rstrip('/'),
    'model': sys.argv[4],
    'model_json_mode': True,
    'actor': os.environ.get('USER') or 'operator',
    'host': '127.0.0.1',
    'port': port,
    'platform': 'macos',
    'instance_id': 'default',
    'instance_name': 'default',
    'instance_kind': 'system',
})
path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
path.chmod(0o600)
PY

LIGHTHOUSE_CONFIG="$CONFIG_FILE" "$VENV_DIR/bin/python" -c "from lighthouse.instances import ensure_default_instance; ensure_default_instance()"

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
    <key>LIGHTHOUSE_INSTANCE_ID</key><string>default</string>
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
ln -sfn "$VENV_DIR/bin/lh" "$BREW_PREFIX/bin/lh"

lighthouse_ready() {
  curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1
}

wait_for_lighthouse() {
  local attempts="${1:-60}" attempt
  for ((attempt = 0; attempt < attempts; attempt++)); do
    lighthouse_ready && return 0
    sleep 1
  done
  return 1
}

start_lighthouse_service_with_recovery() {
  local domain="gui/$(id -u)" target="gui/$(id -u)/$LABEL"
  launchctl bootstrap "$domain" "$PLIST" >/dev/null 2>&1 || true
  launchctl kickstart -k "$target" >/dev/null 2>&1 || true
  wait_for_lighthouse 30 && return 0

  say "LightHouse service startup issue detected; repairing automatically"
  launchctl bootout "$target" >/dev/null 2>&1 || true
  launchctl bootout "$domain" "$PLIST" >/dev/null 2>&1 || true
  sleep 1
  launchctl bootstrap "$domain" "$PLIST" >/dev/null 2>&1 || return 1
  launchctl kickstart -k "$target" >/dev/null 2>&1 || true
  wait_for_lighthouse 60
}

: > "$LOG_DIR/server.log"
: > "$LOG_DIR/server-error.log"
say "Starting the default LightHouse instance on port $PORT"
if ! start_lighthouse_service_with_recovery; then
  show_server_logs
  fail "LightHouse did not become healthy; inspect $LOG_DIR/server-error.log"
fi

if ! MIGRATE_OUTPUT="$("$BREW_PREFIX/bin/lh" migrate 2>&1)"; then
  [[ -n "$MIGRATE_OUTPUT" ]] && printf '%s\n' "$MIGRATE_OUTPUT" >&2
  show_server_logs
  fail "LightHouse database migration failed"
fi
if ! "$BREW_PREFIX/bin/lh" doctor; then
  show_server_logs
  fail "LightHouse diagnostics did not pass"
fi

say "LightHouse is installed."
printf "\nDefault instance: http://127.0.0.1:%s\n\n" "$PORT"
printf "Open any project and run:\n\n  cd /path/to/project\n  lh\n\n"
printf "Open another independent instance with:\n\n  lh new\n\n"
if ! command -v lh >/dev/null 2>&1; then
  printf 'Refresh this shell once with:\n\n  exec zsh -l\n\n'
fi
