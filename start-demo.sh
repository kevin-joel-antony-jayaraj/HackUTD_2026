#!/usr/bin/env bash
# PenguinRE demo: starts the Photon poller and the API in the background, then the dashboard.
#   ./start-demo.sh           start everything (Ctrl-C stops all three)
#   ./start-demo.sh --reset   reload the demo portfolio and real weather first
# Logs: logs/api.log, logs/poller.log. Dashboard: http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
BACKEND="$ROOT/sitesignal-backend"
PY="$BACKEND/.venv/bin/python"

[ -x "$PY" ] || { echo "Missing $BACKEND/.venv. Run: cd sitesignal-backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f "$BACKEND/.env" ] || { echo "Missing sitesignal-backend/.env (copy .env.example and fill it in)"; exit 1; }
[ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && npm install)
pg_isready -q -h localhost || { echo "Postgres isn't running. Start it: brew services start postgresql@18"; exit 1; }
for port in 8000 5173; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "Port $port is in use (an old demo still running?). Stop it first."; exit 1; fi
done

cd "$BACKEND"
if [ "${1:-}" = "--reset" ]; then
  echo "Resetting the demo portfolio..."
  "$PY" -m sitesignal.jobs seed >/dev/null
  "$PY" -m sitesignal.jobs weather | grep -E "sites_updated|failed"
fi

mkdir -p "$ROOT/logs"
# SCHEDULER=off: the sweep runs when you press "Run sweep now", not automatically at startup
SCHEDULER=off "$PY" -m uvicorn api.main:app --port 8000 >"$ROOT/logs/api.log" 2>&1 &
API_PID=$!
"$PY" -u -m sitesignal.jobs poll >"$ROOT/logs/poller.log" 2>&1 &
POLL_PID=$!
trap 'echo; echo "Stopping..."; kill $API_PID $POLL_PID 2>/dev/null; exit 0' INT TERM EXIT

for _ in $(seq 1 30); do curl -sf localhost:8000/health >/dev/null && break; sleep 1; done
curl -sf localhost:8000/health >/dev/null || { echo "API didn't start; see logs/api.log"; exit 1; }
echo "API on http://localhost:8000 and Photon poller running. Dashboard: http://localhost:5173"

cd "$ROOT/frontend"
npx vite --port 5173 --strictPort
