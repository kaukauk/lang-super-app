#!/usr/bin/env bash
# Launch the Korean Immersion app (backend serves the built React UI).
#   ./run.sh              - build the UI if needed, then serve at http://127.0.0.1:8770
#   ./run.sh --build      - force a fresh UI build first
#   ./run.sh --tailscale  - also publish over Tailscale (HTTPS, tailnet-only)
#   ./run.sh --tailscale-off - remove the Tailscale publication and exit
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/node/bin:$PATH"

PORT=8770
TS_PORT="${TS_PORT:-8443}"   # :443 on this node is already used by another service
TAILSCALE=0
BUILD=0

for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --tailscale) TAILSCALE=1 ;;
    --tailscale-off)
      tailscale serve --https="$TS_PORT" off
      echo "==> Tailscale publication removed"
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$BUILD" = 1 ] || [ ! -d "$ROOT/frontend/dist" ]; then
  echo "==> Building frontend"
  (cd "$ROOT/frontend" && npm install && npm run build)
fi

if [ "$TAILSCALE" = 1 ]; then
  host="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  # Proxy from the tailnet to the loopback listener: nothing is exposed to the LAN.
  tailscale serve --bg --https="$TS_PORT" "http://127.0.0.1:$PORT"
  echo "==> Tailnet URL: https://$host:$TS_PORT  (./run.sh --tailscale-off to unpublish)"
fi

# ctranslate2 dlopens libcudnn/libcublas by name at first inference. The CUDA libs
# are pip-installed inside the venv, so without this they aren't found and
# transcribe.py silently falls back to CPU (~30x slower) instead of erroring.
CUDA_LIBS="$(ls -d "$ROOT"/backend/.venv/lib/python*/site-packages/nvidia/*/lib 2>/dev/null | tr '\n' ':')"
[ -n "$CUDA_LIBS" ] && export LD_LIBRARY_PATH="${CUDA_LIBS}${LD_LIBRARY_PATH}"

echo "==> Backend on http://127.0.0.1:$PORT  (Ctrl-C to stop)"
cd "$ROOT/backend"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
