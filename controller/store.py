"""SQLite-backed durable state: checkpoint, stats, audit, proposals."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_events (
  event_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS call_stats (
  workflow_id TEXT NOT NULL,
  cohort_id TEXT NOT NULL,
  dim TEXT NOT NULL,
  value TEXT NOT NULL,
  window TEXT NOT NULL,          -- 'all' | 'recent'
  dials INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0,
  engaged INTEGER NOT NULL DEFAULT 0,
  halluc INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (workflow_id, cohort_id, dim, value, window)
);

CREATE TABLE IF NOT EXISTS hour_stats (
  workflow_id TEXT NOT NULL,
  cohort_id TEXT NOT NULL,
  hour INTEGER NOT NULL,
  dials INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (workflow_id, cohort_id, hour)
);

CREATE TABLE IF NOT EXISTS person_hour (
  workflow_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  hour INTEGER NOT NULL,
  dials INTEGER NOT NULL DEFAULT 0,
  connected INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (workflow_id, person_id, hour)
);

CREATE TABLE IF NOT EXISTS leads (
  lead_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  features TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  approval_id TEXT,
  status TEXT NOT NULL,
  kind TEXT NOT NULL,
  base_version INTEGER,
  payload TEXT NOT NULL,
  created_ts TEXT,
  updated_ts TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  sim_ts TEXT,
  workflow_id TEXT,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
  workflow_id TEXT NOT NULL,
  dim TEXT NOT NULL,
  phase TEXT NOT NULL,
  started_day INTEGER,
  promoted_value TEXT,
  exposure_days INTEGER DEFAULT 0,
  PRIMARY KEY (workflow_id, dim)
);

CREATE TABLE IF NOT EXISTS incident_state (
  workflow_id TEXT PRIMARY KEY,
  halluc_active INTEGER NOT NULL DEFAULT 0,
  last_action_day INTEGER,
  validation_run_id TEXT,
  candidate_prompt TEXT,
  cooldown_until_day INTEGER
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- meta / checkpoint -------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row["value"])

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def checkpoint(self, **fields: Any) -> None:
        for k, v in fields.items():
            self.set_meta(k, v)
        self.conn.commit()

    # --- events ------------------------------------------------------------
    def mark_events(self, event_ids: Iterable[int]) -> int:
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO seen_events(event_id) VALUES (?)",
            [(int(e),) for e in event_ids],
        )
        return cur.rowcount if cur.rowcount is not None else 0

    def max_seen_event(self) -> int:
        row = self.conn.execute("SELECT MAX(event_id) AS m FROM seen_events").fetchone()
        return int(row["m"] or -1)

    # --- stats -------------------------------------------------------------
    def bump_call(
        self,
        workflow_id: str,
        cohort_id: str,
        dim: str,
        value: str,
        connected: bool,
        engaged: bool,
        halluc: bool,
        windows: tuple[str, ...] = ("all", "recent"),
    ) -> None:
        for window in windows:
            self.conn.execute(
                """
                INSERT INTO call_stats(workflow_id,cohort_id,dim,value,window,dials,connected,engaged,halluc)
                VALUES(?,?,?,?,?,1,?,?,?)
                ON CONFLICT(workflow_id,cohort_id,dim,value,window) DO UPDATE SET
                  dials=dials+1,
                  connected=connected+excluded.connected,
                  engaged=engaged+excluded.engaged,
                  halluc=halluc+excluded.halluc
                """,
                (
                    workflow_id,
                    cohort_id,
                    dim,
                    value,
                    window,
                    int(connected),
                    int(engaged),
                    int(halluc),
                ),
            )

    def reset_recent(self) -> None:
        self.conn.execute("DELETE FROM call_stats WHERE window='recent'")

    def get_stats(
        self,
        workflow_id: str,
        dim: str,
        window: str = "all",
        cohort_id: str | None = None,
    ) -> list[dict]:
        if cohort_id is None:
            rows = self.conn.execute(
                """
                SELECT value,
                       SUM(dials) AS dials,
                       SUM(connected) AS connected,
                       SUM(engaged) AS engaged,
                       SUM(halluc) AS halluc
                FROM call_stats
                WHERE workflow_id=? AND dim=? AND window=?
                GROUP BY value
                """,
                (workflow_id, dim, window),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT value, dials, connected, engaged, halluc
                FROM call_stats
                WHERE workflow_id=? AND cohort_id=? AND dim=? AND window=?
                """,
                (workflow_id, cohort_id, dim, window),
            ).fetchall()
        return [dict(r) for r in rows]

    def bump_hour(self, workflow_id: str, cohort_id: str, hour: int, connected: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO hour_stats(workflow_id,cohort_id,hour,dials,connected)
            VALUES(?,?,?,1,?)
            ON CONFLICT(workflow_id,cohort_id,hour) DO UPDATE SET
              dials=dials+1, connected=connected+excluded.connected
            """,
            (workflow_id, cohort_id, hour, int(connected)),
        )

    def get_hour_stats(self, workflow_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT hour, SUM(dials) AS dials, SUM(connected) AS connected
            FROM hour_stats WHERE workflow_id=? GROUP BY hour
            """,
            (workflow_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def bump_person_hour(self, workflow_id: str, person_id: str, hour: int, connected: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO person_hour(workflow_id,person_id,hour,dials,connected)
            VALUES(?,?,?,1,?)
            ON CONFLICT(workflow_id,person_id,hour) DO UPDATE SET
              dials=dials+1, connected=connected+excluded.connected
            """,
            (workflow_id, person_id, hour, int(connected)),
        )

    def best_hours_for_people(self, workflow_id: str, min_dials: int = 4, limit: int = 5000) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT person_id, hour, dials, connected,
                   CAST(connected AS REAL)/dials AS rate
            FROM person_hour
            WHERE workflow_id=? AND dials>=?
            ORDER BY person_id, rate DESC, dials DESC
            """,
            (workflow_id, min_dials),
        ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            pid = r["person_id"]
            if pid in out:
                continue
            if r["rate"] >= 0.05:  # any signal better than nothing
                out[pid] = int(r["hour"])
            if len(out) >= limit:
                break
        return out

    def upsert_lead(self, lead_id: str, workflow_id: str, person_id: str, features: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO leads(lead_id,workflow_id,person_id,features) VALUES(?,?,?,?)
            ON CONFLICT(lead_id) DO NOTHING
            """,
            (lead_id, workflow_id, person_id, json.dumps(features)),
        )

    def lead_count(self, workflow_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        return int(row["n"])

    # --- proposals ---------------------------------------------------------
    def save_proposal(
        self,
        proposal_id: str,
        workflow_id: str,
        status: str,
        kind: str,
        payload: dict,
        approval_id: str | None = None,
        base_version: int | None = None,
        ts: str | None = None,
    ) -> None:
        now = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.conn.execute(
            """
            INSERT INTO proposals(proposal_id,workflow_id,approval_id,status,kind,base_version,payload,created_ts,updated_ts)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(proposal_id) DO UPDATE SET
              approval_id=COALESCE(excluded.approval_id, approval_id),
              status=excluded.status,
              payload=excluded.payload,
              updated_ts=excluded.updated_ts
            """,
            (
                proposal_id,
                workflow_id,
                approval_id,
                status,
                kind,
                base_version,
                json.dumps(payload),
                now,
                now,
            ),
        )

    def update_proposal_status(self, proposal_id: str, status: str, approval_id: str | None = None) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if approval_id:
            self.conn.execute(
                "UPDATE proposals SET status=?, approval_id=?, updated_ts=? WHERE proposal_id=?",
                (status, approval_id, now, proposal_id),
            )
        else:
            self.conn.execute(
                "UPDATE proposals SET status=?, updated_ts=? WHERE proposal_id=?",
                (status, now, proposal_id),
            )

    def open_proposals(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM proposals WHERE status IN ('pending','approved')"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def recent_proposal_count(self, workflow_id: str, since_iso_day: str | None = None) -> int:
        # Count non-terminal proposals recently created — approximate from status history.
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM proposals
            WHERE workflow_id=? AND status IN ('pending','approved','applied','rejected','expired','lapsed')
            """,
            (workflow_id,),
        ).fetchone()
        return int(row["n"])

    # --- experiments / incidents ------------------------------------------
    def get_experiment(self, workflow_id: str, dim: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM experiments WHERE workflow_id=? AND dim=?",
            (workflow_id, dim),
        ).fetchone()
        return dict(row) if row else None

    def set_experiment(self, workflow_id: str, dim: str, **fields: Any) -> None:
        cur = self.get_experiment(workflow_id, dim) or {
            "workflow_id": workflow_id,
            "dim": dim,
            "phase": "explore",
            "started_day": 0,
            "promoted_value": None,
            "exposure_days": 0,
        }
        cur.update(fields)
        self.conn.execute(
            """
            INSERT INTO experiments(workflow_id,dim,phase,started_day,promoted_value,exposure_days)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(workflow_id,dim) DO UPDATE SET
              phase=excluded.phase,
              started_day=excluded.started_day,
              promoted_value=excluded.promoted_value,
              exposure_days=excluded.exposure_days
            """,
            (
                workflow_id,
                dim,
                cur["phase"],
                cur["started_day"],
                cur["promoted_value"],
                cur["exposure_days"],
            ),
        )

    def get_incident(self, workflow_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM incident_state WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        if row:
            return dict(row)
        self.conn.execute(
            "INSERT INTO incident_state(workflow_id) VALUES (?)", (workflow_id,)
        )
        return {
            "workflow_id": workflow_id,
            "halluc_active": 0,
            "last_action_day": None,
            "validation_run_id": None,
            "candidate_prompt": None,
            "cooldown_until_day": None,
        }

    def set_incident(self, workflow_id: str, **fields: Any) -> None:
        cur = self.get_incident(workflow_id)
        cur.update(fields)
        self.conn.execute(
            """
            INSERT INTO incident_state(workflow_id,halluc_active,last_action_day,validation_run_id,candidate_prompt,cooldown_until_day)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(workflow_id) DO UPDATE SET
              halluc_active=excluded.halluc_active,
              last_action_day=excluded.last_action_day,
              validation_run_id=excluded.validation_run_id,
              candidate_prompt=excluded.candidate_prompt,
              cooldown_until_day=excluded.cooldown_until_day
            """,
            (
                workflow_id,
                int(cur.get("halluc_active") or 0),
                cur.get("last_action_day"),
                cur.get("validation_run_id"),
                cur.get("candidate_prompt"),
                cur.get("cooldown_until_day"),
            ),
        )

    # --- audit -------------------------------------------------------------
    def audit(
        self,
        kind: str,
        summary: str,
        detail: dict | None = None,
        workflow_id: str | None = None,
        sim_ts: str | None = None,
    ) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.conn.execute(
            "INSERT INTO audit(ts,sim_ts,workflow_id,kind,summary,detail) VALUES(?,?,?,?,?,?)",
            (now, sim_ts, workflow_id, kind, summary, json.dumps(detail or {})),
        )

    def query_audit(
        self,
        workflow_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        q = "SELECT * FROM audit WHERE 1=1"
        args: list[Any] = []
        if workflow_id:
            q += " AND workflow_id=?"
            args.append(workflow_id)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"])
            out.append(d)
        return out

    def commit(self) -> None:
        self.conn.commit()
