#!/usr/bin/env bash
# Demonstrates crash safety: kill the controller mid-run, restart, finish, grade.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCENARIO="${1:-scenarios/public_01_dev.yaml}"
PORT="${PORT:-8742}"
RUN_DIR="runs/crash_demo"
DATA_DIR="controller_data_crash"
BASE_DIR="runs/crash_baseline"
REPORT="reports/crash_recovery.md"
LOG="reports/crash_recovery.log"

rm -rf "$RUN_DIR" "$DATA_DIR" "$BASE_DIR"
mkdir -p reports runs

PY="${PYTHON:-python3}"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"

echo "== baseline ==" | tee "$LOG"
"$PY" -m sim baseline --scenario "$SCENARIO" --run-dir "$BASE_DIR" | tee -a "$LOG"

echo "== serve ==" | tee -a "$LOG"
"$PY" -m sim serve --scenario "$SCENARIO" --run-dir "$RUN_DIR" --port "$PORT" --force &
SIM_PID=$!
cleanup() { kill "$SIM_PID" 2>/dev/null || true; }
trap cleanup EXIT

for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; then break; fi
  sleep 1
done

echo "== controller start ==" | tee -a "$LOG"
CONTROLLER_DATA="$DATA_DIR" ./run.sh "http://127.0.0.1:$PORT" > reports/crash_controller_1.log 2>&1 &
CTL_PID=$!

# Wait until durable state exists (history bootstrap committed), then kill mid-run.
for i in $(seq 1 120); do
  if [[ -f "$DATA_DIR/controller.db" ]]; then
    N=$("$PY" - <<'PY'
import sqlite3, sys
try:
  c=sqlite3.connect("controller_data_crash/controller.db")
  n=c.execute("select count(*) from seen_events").fetchone()[0]
  ts=c.execute("select value from meta where key='last_ts'").fetchone()
  print(n if ts else 0)
except Exception:
  print(0)
PY
)
    if [[ "${N:-0}" -gt 1000 ]]; then
      echo "progress: seen_events=$N — killing" | tee -a "$LOG"
      break
    fi
  fi
  sleep 1
done
sleep 2
echo "== killing controller pid $CTL_PID ==" | tee -a "$LOG"
kill -9 "$CTL_PID" || true
wait "$CTL_PID" 2>/dev/null || true
echo "controller killed; sqlite state retained in $DATA_DIR" | tee -a "$LOG"
ls -la "$DATA_DIR" | tee -a "$LOG"
echo "resume marker BEFORE restart:" | tee -a "$LOG"
"$PY" - <<PY | tee -a "$LOG"
import sqlite3
c=sqlite3.connect("$DATA_DIR/controller.db")
for k in ("last_chunk","last_ts","bootstrapped"):
    row=c.execute("select value from meta where key=?",(k,)).fetchone()
    print(k, row[0] if row else None)
print("seen_events", c.execute("select count(*) from seen_events").fetchone()[0])
print("open_proposals", c.execute("select count(*) from proposals where status in ('pending','approved')").fetchone()[0])
print("audit_rows", c.execute("select count(*) from audit").fetchone()[0])
PY

echo "== restart controller ==" | tee -a "$LOG"
CONTROLLER_DATA="$DATA_DIR" ./run.sh "http://127.0.0.1:$PORT" > reports/crash_controller_2.log 2>&1
echo "== controller finished after restart ==" | tee -a "$LOG"
"$PY" -m controller.audit_query --data-dir "$DATA_DIR" --kind resume --limit 5 | tee -a "$LOG" || true
"$PY" -m controller.audit_query --data-dir "$DATA_DIR" --kind bootstrap --limit 5 | tee -a "$LOG" || true

"$PY" -m sim grade --run "$RUN_DIR" --baseline "$BASE_DIR" --out "$REPORT" | tee -a "$LOG"
echo "audit sample:" | tee -a "$LOG"
"$PY" -m controller.audit_query --data-dir "$DATA_DIR" --limit 15 | tee -a "$LOG"
echo "OK — see $REPORT and $LOG"
