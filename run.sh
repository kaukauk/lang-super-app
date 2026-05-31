#!/usr/bin/env bash
# Launch the Korean Immersion app (backend serves the built React UI).
#   ./run.sh           - build the UI if needed, then serve at http://127.0.0.1:8765
#   ./run.sh --build   - force a fresh UI build first
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/node/bin:$PATH"

if [ "$1" = "--build" ] || [ ! -d "$ROOT/frontend/dist" ]; then
  echo "==> Building frontend"
  (cd "$ROOT/frontend" && npm install && npm run build)
fi

echo "==> Backend on http://127.0.0.1:8765  (Ctrl-C to stop)"
cd "$ROOT/backend"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
