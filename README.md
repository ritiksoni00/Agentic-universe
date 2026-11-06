# Outcome-First Agent Loop (Formi SDE assignment)

Controller that continuously improves outbound voice-agent workflows: detect drift, experiment on populations, propose changes through governance, apply what is approved, and explain every decision from its own store.

This repo includes the assignment simulator (`sim/`) unchanged, plus our controller under `controller/`.

## Quick start

```bash
python3 -m pip install -r requirements.txt

# Baseline (no-op) for comparison
python3 -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base_public_01

# Terminal A — simulator
python3 -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine_public_01 --port 8741 --force

# Terminal B — controller (runs until the scenario finishes)
./run.sh http://127.0.0.1:8741

# Grade
python3 -m sim grade --run runs/mine_public_01 --baseline runs/base_public_01 --out reports/public_01.md
```

Repeat with `scenarios/public_02_incidents.yaml` for the incident scenario.

Crash recovery demo:

```bash
bash scripts/crash_recovery_demo.sh scenarios/public_01_dev.yaml
```

Audit (“why did workflow X change?”):

```bash
python3 -m controller.audit_query --data-dir controller_data --workflow nw_renewal --limit 30
```

## What the controller does

1. **Ingest** history + live NDJSON chunks into SQLite (idempotent on `event_id`).
2. **Cohorts** — one shared object (language / age slices) used for stats and allocations.
3. **Experiments** — explore welcome/voice uniformly, promote with Wilson + two-proportion z-tests, soft-rollback on decay.
4. **Caller ID health** — rotate off DIDs with large pickup gaps (auto-apply when policy allows).
5. **Hallucination incidents** — especially after supervisor prompt edits: LLM suggest → validate → approval with evidence.
6. **Governance** — approvals, apply windows, stale-base re-propose, auto-apply only inside policy.
7. **Crash safety** — kill mid-run, restart with the same `CONTROLLER_DATA` directory; resumes without double-apply.

Details, stakeholder replies, and grade tables: **[`SUBMISSION.md`](SUBMISSION.md)**. Simulator contract: [`docs/SIMULATOR.md`](docs/SIMULATOR.md).

## Layout

```
controller/           our always-on loop + SQLite store
sim/                  closed-loop simulator (do not modify for submission runs)
scenarios/            public_01 / public_02 / public_03
stakeholders/         six memos
run.sh                ./run.sh <SIM_URL>
scripts/crash_recovery_demo.sh
reports/              grade outputs + load/crash notes
SUBMISSION.md         evaluation write-up
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CONTROLLER_DATA` | `./controller_data` | Durable SQLite + resume state |
| `TICK_MINUTES` | `60` | Simulated minutes per `/sim/advance` |
| `PYTHON` | `python3` | Interpreter for `run.sh` |

No extra services. No Docker required. Designed for a 4-vCPU / 8 GB Linux box.
