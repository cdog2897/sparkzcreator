#!/bin/bash

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$APP_DIR/frontend"
APP_URL="http://127.0.0.1:5173"
API_URL="http://127.0.0.1:8000"
api_pid=""
frontend_pid=""

cleanup() {
  trap - EXIT INT TERM
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

  echo "App stopped."
}

fail() {
  echo
  echo "Could not start the app: $1"
  echo "Press Return to close this window."
  read -r _
  exit 1
}

wait_for_url() {
  local url="$1"
  local pid="$2"
  local label="$3"
  local attempt

  for attempt in $(seq 1 60); do
    if curl --silent --fail --output /dev/null "$url"; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    sleep 0.25
  done

  echo "$label took too long to start."
  return 1
}

trap cleanup EXIT
trap 'exit 0' INT TERM

echo "Starting Sparkz Creator..."

command -v npm >/dev/null 2>&1 || fail "Node.js/npm is not installed."
command -v curl >/dev/null 2>&1 || fail "curl is not installed."

python_is_supported() {
  [ -x "$1" ] && \
    "$1" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >/dev/null 2>&1
}

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
      echo "Preparing the app's Python environment..."
      "$candidate" -m venv "$APP_DIR/.venv" || \
        fail "The app's Python environment could not be created."
      PYTHON_BIN="$APP_DIR/.venv/bin/python"
      break
    fi
  done
fi

[ -n "$PYTHON_BIN" ] || \
  fail "Python 3.10 or newer is required. Install current Python with Homebrew and try again."

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, yt_dlp; parts = yt_dlp.version.__version__.split('.'); raise SystemExit(len(parts) < 4 or tuple(map(int, parts[:3])) < (2026, 6, 9))" >/dev/null 2>&1; then
  echo "Installing or updating Python dependencies..."
  "$PYTHON_BIN" -m pip install -r "$APP_DIR/requirements.txt" || \
    fail "Python dependencies could not be installed."
  "$PYTHON_BIN" -m pip install -U --pre "yt-dlp[default]" || \
    fail "The Instagram importer could not be updated."
fi

if [ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]; then
  echo "Installing frontend dependencies (first run only)..."
  (cd "$FRONTEND_DIR" && npm install) || \
    fail "Frontend dependencies could not be installed."
fi

if curl --silent --output /dev/null "$API_URL" || \
  curl --silent --output /dev/null "$APP_URL"; then
  fail "Port 8000 or 5173 is already in use. Stop the other app and try again."
fi

echo "Starting API..."
(cd "$APP_DIR" && exec "$PYTHON_BIN" -m uvicorn web_app:app --host 127.0.0.1 --port 8000) &
api_pid=$!
wait_for_url "$API_URL/api/bootstrap" "$api_pid" "The API" || \
  fail "The API did not start."

echo "Starting website..."
(cd "$FRONTEND_DIR" && exec ./node_modules/.bin/vite --host 127.0.0.1 --port 5173) &
frontend_pid=$!
wait_for_url "$APP_URL" "$frontend_pid" "The website" || \
  fail "The website did not start."

echo
echo "The app is ready: $APP_URL"
echo "Keep this window open while using the app."
echo "Press Control-C here when you want to stop it."
echo

if [ "${RUN_APP_NO_BROWSER:-0}" != "1" ]; then
  open "$APP_URL" >/dev/null 2>&1 || true
fi

while kill -0 "$api_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

fail "One of the app services stopped unexpectedly."
