#!/usr/bin/env bash
# Contract: ./run.sh <SIM_URL> starts the controller and runs until the scenario finishes.
# No Docker required. State persists under CONTROLLER_DATA (default: controller_data/).
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export SIM_URL
export CONTROLLER_DATA="${CONTROLLER_DATA:-$ROOT/controller_data}"
mkdir -p "$CONTROLLER_DATA"
# Prefer the repo venv if present; otherwise system / user site-packages.
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
export PYTHONPATH="${PYTHONPATH:-$ROOT}"
exec "$PY" -m controller --url "$SIM_URL" --data-dir "$CONTROLLER_DATA" --tick "${TICK_MINUTES:-60}"
