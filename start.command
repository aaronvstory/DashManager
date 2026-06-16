#!/bin/bash
# DashManager launcher (macOS / Linux). Double-click in Finder, or run:
#   ./start.command
# Starts the backend + built frontend on http://127.0.0.1:8765.
# (Windows users: double-click start.bat instead.)
set -e

# cd to the repo root (this script's dir) so relative paths resolve.
cd "$(dirname "$0")"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] DashManager starting from $(pwd)"

URL="http://127.0.0.1:8765"

# If the app is already serving on :8765, just open the browser and exit —
# don't try to bind the port again (which would error out). lsof is on macOS;
# fall back to nc if present, else skip the check and let the bind fail loudly.
already_running() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 8765 >/dev/null 2>&1
  else
    return 1
  fi
}
if already_running; then
  echo "DashManager already running - opening $URL"
  open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true
  exit 0
fi

# POSIX venvs put the interpreter at .venv/bin/python.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ERROR: .venv missing. Create it first, e.g.:"
  echo "  uv venv .venv && uv pip install --python .venv -e \".[dev]\""
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Opening $URL ..."
# `open` exists on macOS; fall back to xdg-open on Linux, else skip.
( sleep 1; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true ) &

# Always start via `python -m backend` — it installs the right event-loop policy
# before uvicorn (the uvicorn CLI breaks Playwright). Proactor is Windows-only;
# on POSIX this is a no-op, so the same entrypoint is correct everywhere.
exec "$PY" -m backend
