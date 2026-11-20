#!/bin/bash

set -u

if [ -n "${BASH_SOURCE[0]:-}" ]; then
  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  APP_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
FRONTEND_DIR="$APP_DIR/frontend"
API_PORT="${SPARKZ_API_PORT:-8000}"
WEB_PORT="${SPARKZ_WEB_PORT:-5173}"
API_URL="http://127.0.0.1:${API_PORT}"
APP_URL="http://127.0.0.1:${WEB_PORT}"
LOCK_DIR="${SPARKZ_LOCK_DIR:-${TMPDIR:-/tmp}/sparkz-creator-${UID}.lock}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-5}"
api_pid=""
frontend_pid=""
adopted_running=0
owns_lock=0

cleanup() {
  trap - EXIT INT TERM
  if [ "$adopted_running" = "1" ]; then
    return
  fi

  echo
  echo "Stopping the app..."

  if [ -n "$frontend_pid" ]; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
  if [ -n "$api_pid" ]; then
    kill "$api_pid" 2>/dev/null || true
  fi

  if [ -n "$frontend_pid" ]; then
    wait "$frontend_pid" 2>/dev/null || true
  fi
  if [ -n "$api_pid" ]; then
    wait "$api_pid" 2>/dev/null || true
  fi

  if [ "$owns_lock" = "1" ]; then
    rm -rf "$LOCK_DIR"
  fi

  echo "App stopped."
}

fail() {
  echo
  echo "Could not start the app: $1"
  if [ -t 0 ] && [ "${RUN_APP_NO_BROWSER:-0}" != "1" ]; then
    echo "Press Return to close this window."
    read -r _ || true
  fi
  exit 1
}

run_with_heartbeat() {
  local message="$1"
  shift
  local pid=""
  local ticks=0
  local step_ticks=$((HEARTBEAT_SECONDS * 4))
  echo "$message"
  if [ "$step_ticks" -lt 4 ]; then
    step_ticks=4
  fi
  if [ "${HEARTBEAT_QUIET:-0}" = "1" ]; then
    "$@" >/dev/null 2>&1 &
  else
    "$@" &
  fi
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 0.25
    ticks=$((ticks + 1))
    if [ $((ticks % step_ticks)) -eq 0 ] && kill -0 "$pid" 2>/dev/null; then
      echo "Still working: $message"
    fi
  done
  wait "$pid"
}

port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -H -lptn "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u
  fi
}

is_our_process() {
  local pid="$1"
  local cmd=""
  local cwd=""
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  case "$cmd" in
    *"$APP_DIR"*) return 0 ;;
  esac
  if [ -L "/proc/$pid/cwd" ]; then
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  elif command -v lsof >/dev/null 2>&1; then
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  fi
  case "$cwd" in
    "$APP_DIR"|"$APP_DIR"/*) return 0 ;;
  esac
  return 1
}

port_owner_kind() {
  local port="$1"
  local pid=""
  local pids=""
  pids="$(port_pids "$port")"
  if [ -z "$pids" ]; then
    echo free
    return
  fi
  for pid in $pids; do
    if ! is_our_process "$pid"; then
      echo foreign
      return
    fi
  done
  echo ours
}

listener_summary() {
  local port="$1"
  local pid=""
  local pids=""
  pids="$(port_pids "$port")"
  if [ -z "$pids" ]; then
    echo "port $port is free"
    return
  fi
  for pid in $pids; do
    echo "port $port pid $pid: $(ps -p "$pid" -o args= 2>/dev/null || echo unknown)"
  done
}

api_is_ready() {
  local body=""
  body="$(curl --silent --fail --max-time 2 "$API_URL/api/bootstrap" 2>/dev/null || true)"
  case "$body" in
    *'"formats"'*) return 0 ;;
    *) return 1 ;;
  esac
}

web_is_ready() {
  local body=""
  body="$(curl --silent --fail --max-time 2 "$APP_URL" 2>/dev/null || true)"
  case "$body" in
    *"Sparkz Creator"*) return 0 ;;
    *) return 1 ;;
  esac
}

app_is_ready() {
  api_is_ready && web_is_ready
}

startup_plan() {
  local api_kind=""
  local web_kind=""
  if app_is_ready; then
    echo adopt
    return
  fi
  api_kind="$(port_owner_kind "$API_PORT")"
  web_kind="$(port_owner_kind "$WEB_PORT")"
  if [ "$api_kind" = free ] && [ "$web_kind" = free ]; then
    echo start
    return
  fi
  if [ "$api_kind" != foreign ] && [ "$web_kind" != foreign ]; then
    echo replace
    return
  fi
  echo busy
}

finish_if_already_running() {
  adopted_running=1
  if [ "$owns_lock" = "1" ]; then
    rm -rf "$LOCK_DIR"
    owns_lock=0
  fi
  echo "Sparkz Creator is already running at $APP_URL"
  echo "Opening the existing app."
  if [ "${RUN_APP_NO_BROWSER:-0}" != "1" ] && command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  fi
  if [ -t 0 ] && [ "${RUN_APP_NO_BROWSER:-0}" != "1" ]; then
    echo "This window can close. Press Return to close it."
    read -r _ || true
  fi
  exit 0
}

stop_previous_launchers() {
  local pid=""
  local cmd=""
  if ! command -v pgrep >/dev/null 2>&1; then
    return
  fi
  for pid in $(pgrep -f "$APP_DIR/Run App.command" || true); do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "$PPID" ] && continue
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    case "$cmd" in
      *"bash -c"*|*"bash -lc"*) continue ;;
    esac
    case "$cmd" in
      *"$APP_DIR/Run App.command") ;;
      *) continue ;;
    esac
    echo "Stopping previous Sparkz Creator launcher $pid."
    kill "$pid" 2>/dev/null || true
  done
}

stop_our_listeners() {
  local port=""
  local pid=""
  local ticks=0
  stop_previous_launchers
  for port in "$API_PORT" "$WEB_PORT"; do
    for pid in $(port_pids "$port"); do
      if is_our_process "$pid"; then
        echo "Stopping previous Sparkz Creator process $pid on port $port."
        kill "$pid" 2>/dev/null || true
      fi
    done
  done
  while [ "$ticks" -lt 20 ]; do
    if [ "$(port_owner_kind "$API_PORT")" != ours ] && [ "$(port_owner_kind "$WEB_PORT")" != ours ]; then
      return 0
    fi
    ticks=$((ticks + 1))
    sleep 0.25
  done
  for port in "$API_PORT" "$WEB_PORT"; do
    for pid in $(port_pids "$port"); do
      if is_our_process "$pid"; then
        echo "Force-stopping previous Sparkz Creator process $pid on port $port."
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
  done
}

acquire_startup_lock() {
  local attempt=0
  local holder=""
  local ticks=0
  for attempt in 1 2 3; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ > "$LOCK_DIR/pid"
      owns_lock=1
      echo "Startup lock acquired."
      return 0
    fi
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
      echo "Another Sparkz Creator start is in progress (pid $holder)."
      echo "Waiting for that start to become ready..."
      ticks=0
      while kill -0 "$holder" 2>/dev/null; do
        if app_is_ready; then
          finish_if_already_running
        fi
        ticks=$((ticks + 1))
        if [ $((ticks % 20)) -eq 0 ]; then
          echo "Still waiting for the other Sparkz Creator start..."
        fi
        if [ "$ticks" -ge 2400 ]; then
          fail "The other Sparkz Creator start did not finish."
        fi
        sleep 0.25
      done
      if app_is_ready; then
        finish_if_already_running
      fi
      echo "The other start ended before the app was ready."
    else
      echo "Removing a stale startup lock."
    fi
    rm -rf "$LOCK_DIR"
  done
  fail "Could not start because another Sparkz Creator launch is locked."
}

wait_for_url() {
  local url="$1"
  local pid="$2"
  local label="$3"
  local attempt=0

  for attempt in $(seq 1 120); do
    if curl --silent --fail --max-time 2 --output /dev/null "$url"; then
      echo "$label is ready."
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$label exited before it was ready."
      return 1
    fi
    if [ $((attempt % 10)) -eq 0 ]; then
      echo "Still waiting for $label..."
    fi
    sleep 0.5
  done

  echo "$label took too long to start."
  return 1
}

python_is_supported() {
  [ -x "$1" ] && \
    "$1" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >/dev/null 2>&1
}

main() {
  trap cleanup EXIT
  trap 'exit 0' INT TERM

  export PYTHONUNBUFFERED=1

  echo "Starting Sparkz Creator..."
  echo "Checking whether Sparkz Creator is already running..."
  if app_is_ready; then
    finish_if_already_running
  fi
  echo "No running app found."

  echo "Checking Node.js and curl..."
  command -v npm >/dev/null 2>&1 || fail "Node.js/npm is not installed."
  command -v curl >/dev/null 2>&1 || fail "curl is not installed."
  echo "Node.js is ready ($(npm -v))."

  echo "Looking for Python 3.10 or newer..."
  PYTHON_BIN=""
  if python_is_supported "$APP_DIR/.venv/bin/python"; then
    PYTHON_BIN="$APP_DIR/.venv/bin/python"
  else
    for candidate in \
      /opt/homebrew/bin/python3 \
      /opt/homebrew/bin/python3.14 \
      /opt/homebrew/bin/python3.13 \
      /opt/homebrew/bin/python3.12 \
      /opt/homebrew/bin/python3.11 \
      /opt/homebrew/bin/python3.10 \
      /usr/local/bin/python3 \
      "$(command -v python3 2>/dev/null)"; do
      if python_is_supported "$candidate"; then
        echo "Creating the app's Python environment with $candidate..."
        "$candidate" -m venv "$APP_DIR/.venv" || \
          fail "The app's Python environment could not be created."
        PYTHON_BIN="$APP_DIR/.venv/bin/python"
        break
      fi
    done
  fi

  [ -n "$PYTHON_BIN" ] || \
    fail "Python 3.10 or newer is required. Install current Python with Homebrew and try again."
  echo "Using Python: $PYTHON_BIN"

  if HEARTBEAT_QUIET=1 run_with_heartbeat "Checking Python dependencies..." \
    "$PYTHON_BIN" -c "import fastapi, uvicorn, yt_dlp; parts = yt_dlp.version.__version__.split('.'); raise SystemExit(len(parts) < 4 or tuple(map(int, parts[:3])) < (2026, 6, 9))"; then
    echo "Python dependencies are ready."
  else
    echo "Python dependencies are missing or older than the Instagram importer needs."
    echo "Installing or updating Python dependencies..."
    "$PYTHON_BIN" -m pip install -r "$APP_DIR/requirements.txt" || \
      fail "Python dependencies could not be installed."
    echo "Updating the Instagram importer..."
    "$PYTHON_BIN" -m pip install -U --pre "yt-dlp[default]" || \
      fail "The Instagram importer could not be updated."
    echo "Python dependencies are ready."
  fi

  echo "Checking frontend dependencies..."
  if [ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]; then
    echo "Installing frontend dependencies (first run only)..."
    (cd "$FRONTEND_DIR" && npm install) || \
      fail "Frontend dependencies could not be installed."
  fi
  echo "Frontend dependencies are ready."

  echo "Checking the app ports again..."
  acquire_startup_lock
  if app_is_ready; then
    finish_if_already_running
  fi
  case "$(startup_plan)" in
    adopt)
      finish_if_already_running
      ;;
    replace)
      echo "Replacing a previous Sparkz Creator that is not responding."
      listener_summary "$API_PORT"
      listener_summary "$WEB_PORT"
      stop_our_listeners
      ;;
    busy)
      echo "Something else is using the app ports."
      listener_summary "$API_PORT"
      listener_summary "$WEB_PORT"
      fail "Port $API_PORT or $WEB_PORT is already used by another program."
      ;;
    start)
      echo "Ports $API_PORT and $WEB_PORT are free."
      ;;
  esac

  echo "Starting API on port $API_PORT..."
  (cd "$APP_DIR" && exec "$PYTHON_BIN" -m uvicorn web_app:app --host 127.0.0.1 --port "$API_PORT") &
  api_pid=$!
  wait_for_url "$API_URL/api/bootstrap" "$api_pid" "The API" || \
    fail "The API did not start."

  echo "Starting website on port $WEB_PORT..."
  (cd "$FRONTEND_DIR" && exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$WEB_PORT") &
  frontend_pid=$!
  wait_for_url "$APP_URL" "$frontend_pid" "The website" || \
    fail "The website did not start."

  echo
  echo "The app is ready: $APP_URL"
  echo "Keep this window open while using the app."
  echo "Press Control-C here when you want to stop it."
  echo

  if [ "${RUN_APP_NO_BROWSER:-0}" != "1" ] && command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  fi

  while kill -0 "$api_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
  done

  fail "One of the app services stopped unexpectedly."
}

if [ "${RUN_APP_SOURCE_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

main
