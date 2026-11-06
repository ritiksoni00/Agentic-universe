"""Query the controller audit store: why did workflow X change?

Usage:
  python -m controller.audit_query --workflow nw_renewal --data-dir controller_data
  python -m controller.audit_query --workflow ks_cancel --kind applied
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="controller_data")
    ap.add_argument("--workflow")
    ap.add_argument("--kind")
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()
    store = Store(Path(args.data_dir) / "controller.db")
    rows = store.query_audit(workflow_id=args.workflow, kind=args.kind, limit=args.limit)
    for r in reversed(rows):
        print(
            f"{r['sim_ts'] or r['ts']}  [{r['kind']}]  {r['workflow_id'] or '-'}  {r['summary']}"
        )
        if r["detail"]:
            print("   ", json.dumps(r["detail"], default=str)[:300])
    store.close()


if __name__ == "__main__":
    main()
