#!/usr/bin/env bash
# Start the whole CardioSense stack: PostgreSQL, backend (:8000), console (:5173).
# Usage: scripts/dev.sh            then open http://localhost:5173
#        scripts/dev.sh stop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HOME/.venvs/cardiosense/bin/python"
RUN="$HOME/.cache/cardiosense/run"; mkdir -p "$RUN"

stop() {
  for n in backend frontend; do
    [ -f "$RUN/$n.pid" ] && kill "$(cat "$RUN/$n.pid")" 2>/dev/null || true
    rm -f "$RUN/$n.pid"
  done
  pkill -f "sender/stream_sender.py" 2>/dev/null || true
}
if [ "${1:-}" = "stop" ]; then stop; echo "stopped"; exit 0; fi
stop

pg_isready -q -h localhost || sudo service postgresql start
until pg_isready -q -h localhost; do sleep 1; done

(cd "$ROOT/backend" && nohup "$PY" -m uvicorn cardiosense.main:app --port 8000 >"$RUN/backend.log" 2>&1 </dev/null & echo $! >"$RUN/backend.pid")
(cd "$ROOT/frontend" && nohup npm run dev >"$RUN/frontend.log" 2>&1 </dev/null & echo $! >"$RUN/frontend.pid")

until curl -sf localhost:8000/v1/health >/dev/null; do sleep 1; done
echo "backend  http://localhost:8000  (log: $RUN/backend.log)"
echo "console  http://localhost:5173  (log: $RUN/frontend.log)"
echo "login    clinician@cardiosense.dev / CardioSense!23"
echo "stream   $PY sender/stream_sender.py --file data/records/200.csv --device dev-demo-200 --key demo-key-200 --url ws://localhost:8000"
