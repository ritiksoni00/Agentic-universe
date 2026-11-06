"""Always-on outcome-first controller.

Loop: advance sim → ingest events → detect → experiment / incident / DID health
→ propose via governance → apply approved changes. All durable state is in SQLite
so a kill/restart resumes without double-applying.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .client import SimClient
from .statsutil import choose_winner, detect_decay, did_health_rows, rate, wilson_lower
from .store import Store

IST = timezone(timedelta(hours=5, minutes=30))
DIMS = ("welcome_message", "voice", "caller_id")


def parse_ts(ts: str) -> datetime:
    # 2027-01-04T14:00:00+05:30
    return datetime.fromisoformat(ts)


def day_index(ts: str) -> int:
    """D0 = 2027-01-04 (IST)."""
    t = parse_ts(ts).astimezone(IST)
    base = datetime(2027, 1, 4, tzinfo=IST)
    return (t.date() - base.date()).days


def hour_of(ts: str) -> int:
    return parse_ts(ts).astimezone(IST).hour


def deep_copy(cfg: dict) -> dict:
    return copy.deepcopy(cfg)


def tv_distance(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def changed_dims(old: dict, new: dict) -> list[str]:
    dims = []
    if old.get("cohorts") != new.get("cohorts"):
        dims.append("cohorts")
    if old.get("prompt_version") != new.get("prompt_version"):
        dims.append("prompt_version")
    if old.get("schedule") != new.get("schedule"):
        dims.append("schedule")
    oa, na = old.get("allocations") or {}, new.get("allocations") or {}
    for dim in DIMS:
        omap = {cid: (alloc.get(dim) or {}) for cid, alloc in oa.items()}
        nmap = {cid: (alloc.get(dim) or {}) for cid, alloc in na.items()}
        if omap != nmap:
            dims.append(dim)
    return dims


def primary_kpi(wf: dict) -> str:
    return wf["objective"]["primary"]


def guardrail_kpi(wf: dict) -> str | None:
    g = wf["objective"].get("guardrail") or {}
    if "pickup_rate_max_drop" in g:
        return "pickup_rate"
    if "engagement_rate_max_drop" in g:
        return "engagement_rate"
    return None


def cohort_for_features(cohorts: list[dict], features: dict) -> str:
    for c in cohorts:
        where = c.get("where") or {}
        ok = True
        for feat, vals in where.items():
            if features.get(feat) not in vals:
                ok = False
                break
        if ok:
            return c["id"]
    return "default"


def build_cohorts(wf: dict) -> list[dict]:
    """Shared cohort object used by detection, experiments and proposals.

    Keep fragments coarse enough to fill min sample sizes within a day or two:
    language for multilingual workflows, plus seniors (60+) when age effects matter.
    """
    langs = list(wf.get("languages") or [])
    cohorts: list[dict] = []
    if len(langs) >= 2:
        # Prioritise smaller / distinctive languages first so they don't fall into default.
        for lang in sorted(langs, key=lambda L: (0 if L in ("ta", "kn", "te") else 1, L)):
            if lang in ("ta", "kn"):
                cohorts.append({"id": f"lang_{lang}", "where": {"lang": [lang]}})
            elif lang == "hi" and "en" in langs:
                cohorts.append({"id": "lang_hi", "where": {"lang": ["hi"]}})
    # Seniors often have distinct voice preferences (see assignment effects).
    cohorts.append({"id": "senior", "where": {"age_band": ["60+"]}})
    cohorts.append({"id": "young", "where": {"age_band": ["18-30"]}})
    # Cap at 8 to leave headroom under the 20 limit and keep traffic concentrated.
    return cohorts[:8]


def uniform(options: list[str]) -> dict[str, float]:
    return {o: 1.0 for o in options}


def exploit(value: str) -> dict[str, float]:
    return {value: 1.0}


def explore_or_blend(options: list[str], winner: str | None, explore_share: float = 0.25) -> dict[str, float]:
    if not winner or winner not in options:
        return uniform(options)
    weights = {o: explore_share / max(len(options) - 1, 1) for o in options if o != winner}
    weights[winner] = 1.0 - explore_share
    return weights


class Controller:
    def __init__(self, url: str, data_dir: Path, tick_minutes: int = 60):
        self.client = SimClient(url)
        self.store = Store(data_dir / "controller.db")
        self.tick = tick_minutes
        self.data_dir = data_dir
        self.workflows: dict[str, dict] = {}
        self.lead_features: dict[str, dict] = {}  # lead_id -> features (hot cache)
        self.config_cache: dict[str, dict] = {}  # wid -> {version, config}
        self.pending_apply: dict[str, str] = {}  # wid -> approval_id (also in store)
        self.history_bootstrapped = False
        self.day_seen_for_recent_reset: int | None = None
        self.requests_today: dict[str, list[int]] = {}  # wid -> list of sim-minutes of requests

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self) -> None:
        wfs = self.client.get("/workflows")
        self.workflows = {w["workflow_id"]: w for w in wfs}
        for wid in self.workflows:
            self.store.get_incident(wid)  # ensure row
            cfg = self.client.get(f"/workflows/{wid}/config")
            self.config_cache[wid] = cfg

        last_chunk = self.store.get_meta("last_chunk", -1)
        if last_chunk < 0:
            # Ingest history chunks once.
            hist = self.client.get("/events/chunks", params={"kind": "history", "after": -1})
            for ch in hist:
                self._ingest_chunk(ch["chunk"], kind="history")
            self.store.audit(
                "bootstrap",
                f"ingested {len(hist)} history chunks",
                {"chunks": len(hist), "max_event": self.store.max_seen_event()},
            )
            self.store.checkpoint(last_chunk=max([c["chunk"] for c in hist], default=-1), bootstrapped=True)
        else:
            self.store.audit("resume", f"resuming from chunk {last_chunk}", {"last_chunk": last_chunk})

        # Reconcile open approvals with simulator (crash safety).
        self._reconcile_approvals()
        self.store.commit()

    def _reconcile_approvals(self) -> None:
        for p in self.store.open_proposals():
            aid = p.get("approval_id")
            if not aid:
                continue
            ap = self.client.get_json(f"/approvals/{aid}")
            if not ap:
                self.store.update_proposal_status(p["proposal_id"], "lost")
                continue
            status = ap["status"]
            if status == "approved":
                self.pending_apply[p["workflow_id"]] = aid
                self.store.update_proposal_status(p["proposal_id"], "approved", aid)
            elif status == "pending":
                self.pending_apply[p["workflow_id"]] = aid
                self.store.update_proposal_status(p["proposal_id"], "pending", aid)
            else:
                self.store.update_proposal_status(p["proposal_id"], status, aid)

        # Also poll simulator for any approved not yet applied.
        for wid in self.workflows:
            for ap in self.client.get("/approvals", params={"workflow_id": wid, "status": "approved"}):
                self.pending_apply[wid] = ap["approval_id"]

    # ------------------------------------------------------------------ ingest
    def _ingest_chunk(self, n: int, kind: str = "events") -> None:
        text = self.client.get(f"/events/chunks/{n}")
        for line in text.splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            eid = ev.get("event_id")
            if eid is None:
                continue
            # Idempotent: only process newly inserted event ids (crash mid-chunk safe).
            cur = self.store.conn.execute(
                "INSERT OR IGNORE INTO seen_events(event_id) VALUES (?)", (int(eid),)
            )
            if cur.rowcount == 0:
                continue
            self._handle_event(ev)
        self.store.set_meta("last_chunk", n)
        self.store.commit()

    def _handle_event(self, ev: dict) -> None:
        et = ev["type"]
        if et == "lead":
            self.store.upsert_lead(ev["lead_id"], ev["workflow_id"], ev["person_id"], ev["features"])
            self.lead_features[ev["lead_id"]] = ev["features"]
            return
        if et == "call":
            wid = ev["workflow_id"]
            cohort = ev.get("cohort_id") or "default"
            variant = ev.get("variant") or {}
            connected = bool(ev.get("connected"))
            engaged = bool(ev.get("engaged"))
            qa = ev.get("qa") or {}
            halluc = bool(qa.get("hallucination_flag")) if connected else False
            for dim in DIMS:
                val = variant.get(dim)
                if val is None:
                    continue
                self.store.bump_call(wid, cohort, dim, val, connected, engaged, halluc)
                self.store.bump_call(wid, "ALL", dim, val, connected, engaged, halluc)
            # Prompt-level halluc tracking
            pv = variant.get("prompt_version")
            if pv:
                self.store.bump_call(wid, "ALL", "prompt_version", pv, connected, engaged, halluc)
            hr = hour_of(ev["ts"])
            self.store.bump_hour(wid, cohort, hr, connected)
            self.store.bump_person_hour(wid, ev["person_id"], hr, connected)
            return
        if et == "config_changed":
            wid = ev["workflow_id"]
            actor = ev.get("actor")
            self.store.audit(
                "config_changed_observed",
                f"{wid} → v{ev.get('version')} by {actor}",
                {"event": ev},
                workflow_id=wid,
                sim_ts=ev.get("ts"),
            )
            # Refresh cache; supervisor edits invalidate in-flight experiments.
            cfg = self.client.get(f"/workflows/{wid}/config")
            self.config_cache[wid] = cfg
            if actor == "supervisor":
                dims = ev.get("changed_dims") or []
                # Do not silently overwrite; reset explore phases so we re-learn under new base.
                for dim in ("welcome_message", "voice"):
                    if dim not in dims and "allocations" not in str(dims):
                        continue
                    exp = self.store.get_experiment(wid, dim)
                    if exp and exp.get("phase") == "exploit":
                        self.store.set_experiment(
                            wid, dim, phase="explore", promoted_value=None, exposure_days=0
                        )
                        self.store.set_meta(f"promoted_once:{wid}:{dim}", False)
                        self.store.audit(
                            "experiment_reset",
                            f"supervisor edit reset {dim} experiment on {wid}",
                            {"version": ev.get("version")},
                            workflow_id=wid,
                            sim_ts=ev.get("ts"),
                        )
                if "prompt_version" in dims:
                    # Latch an incident immediately — new prompts have no baseline yet,
                    # so rate-vs-self comparisons would never fire.
                    self.store.set_incident(
                        wid,
                        halluc_active=1,
                        candidate_prompt=None,
                        validation_run_id=None,
                        cooldown_until_day=None,
                        last_action_day=None,
                    )
                    self.store.set_meta(f"prompt_suspect:{wid}", cfg["config"].get("prompt_version"))
                    self.store.audit(
                        "supervisor_prompt_watch",
                        f"supervisor changed prompt on {wid} → {cfg['config'].get('prompt_version')}; watching",
                        {"version": ev.get("version")},
                        workflow_id=wid,
                        sim_ts=ev.get("ts"),
                    )
            return
        if et == "approval_decided":
            self.store.audit(
                "approval_decided",
                f"{ev.get('approval_id')} {ev.get('status')}",
                {"event": ev},
                workflow_id=ev.get("workflow_id"),
                sim_ts=ev.get("ts"),
            )

    # ------------------------------------------------------------------ governance helpers
    def _can_request(
        self, wid: str, sim_minute_approx: int, gov: dict, *, urgent: bool = False
    ) -> bool:
        if wid in self.pending_apply:
            return False
        # Approver hours: only submit during hours.
        # Non-urgent: never before 11:00 (ops standup). Urgent incidents: from open.
        ts = self.store.get_meta("last_ts")
        if ts:
            h = hour_of(ts)
            lo, hi = gov["approver_hours"]
            earliest = lo if urgent else max(lo, 11)
            if h < earliest or h >= hi:
                return False
        # Fatigue: track our own recent requests in the last 24h of sim time.
        recent = [t for t in self.requests_today.get(wid, []) if sim_minute_approx - t < 1440]
        self.requests_today[wid] = recent
        if len(recent) >= gov["max_requests_per_24h"]:
            return False
        return True

    def _estimate_impact(self, wid: str, dim: str, new_val: str, primary: str) -> tuple[float, dict]:
        rows = self.store.get_stats(wid, dim, window="all", cohort_id="ALL")
        by = {r["value"]: r for r in rows}
        if new_val not in by:
            return 0.2, {"note": "no history for candidate"}
        cur_cfg = self.config_cache[wid]["config"]
        cur_alloc = ((cur_cfg.get("allocations") or {}).get("default") or {}).get(dim) or {}
        cur_val = max(cur_alloc, key=cur_alloc.get) if cur_alloc else None
        base = by.get(cur_val) or {"dials": 0, "connected": 0, "engaged": 0}
        cand = by[new_val]
        if primary == "engagement_rate":
            br = rate(int(base["engaged"]), int(base["connected"]))
            cr = rate(int(cand["engaged"]), int(cand["connected"]))
            n = int(cand["connected"])
        else:
            br = rate(int(base["connected"]), int(base["dials"]))
            cr = rate(int(cand["connected"]), int(cand["dials"]))
            n = int(cand["dials"])
        lift = max(0.0, cr - br)
        # Map expected absolute lift into [0,1]; 5pp lift ≈ 1.0
        score = max(0.0, min(1.0, lift / 0.05))
        dry = {
            "baseline_value": cur_val,
            "candidate_value": new_val,
            "baseline_rate": round(br, 4),
            "candidate_rate": round(cr, 4),
            "sample_n": n,
            "expected_abs_lift": round(lift, 4),
            "method": "historical_rate_replay",
        }
        return score, dry

    def _submit_approval(
        self,
        wid: str,
        new_cfg: dict,
        kind: str,
        summary: str,
        rationale: str,
        diff: dict,
        impact: float,
        dry_run: dict,
        evidence: dict | None = None,
        sim_minute: int = 0,
        urgent: bool = False,
    ) -> bool:
        cur = self.client.get(f"/workflows/{wid}/config")
        self.config_cache[wid] = cur
        gov = self.workflows[wid]["governance"]
        if not self._can_request(wid, sim_minute, gov, urgent=urgent or kind == "prompt_fix"):
            return False
        dims = changed_dims(cur["config"], new_cfg)
        if not dims:
            return False

        # Prefer auto-apply when policy allows the full change set.
        # Never auto-apply prompt changes — always need validation evidence via approvals.
        aa = gov.get("auto_apply") or {}
        if (
            aa.get("enabled")
            and "prompt_version" not in dims
            and set(dims).issubset(set(aa.get("allowed_dims") or []))
        ):
            # Check max_shift on allocations.
            ok_shift = True
            old_a, new_a = cur["config"].get("allocations") or {}, new_cfg.get("allocations") or {}
            for cid in set(old_a) | set(new_a):
                for dim in DIMS:
                    o = (old_a.get(cid) or {}).get(dim) or (old_a.get("default") or {}).get(dim) or {}
                    n = (new_a.get(cid) or {}).get(dim) or (new_a.get("default") or {}).get(dim) or {}
                    # Normalise
                    def norm(d):
                        s = sum(d.values()) or 1.0
                        return {k: float(v) / s for k, v in d.items()}

                    if tv_distance(norm(o), norm(n)) > aa.get("max_shift", 0) + 1e-9:
                        ok_shift = False
            if ok_shift and "prompt_version" not in dims:
                pid = f"auto-{uuid.uuid4()}"
                code, body = self.client.put(
                    f"/workflows/{wid}/config",
                    json={
                        "proposal_id": pid,
                        "base_version": cur["version"],
                        "proposed_config": new_cfg,
                        "rationale": rationale,
                        "evidence": evidence or {},
                    },
                )
                self.store.save_proposal(
                    pid, wid, "applied" if code == 200 else "auto_rejected", kind,
                    {"diff": diff, "dry_run": dry_run, "response": body},
                    base_version=cur["version"],
                )
                self.store.audit(
                    "auto_apply",
                    f"{wid} {kind} → {code}",
                    {"body": body, "diff": diff},
                    workflow_id=wid,
                    sim_ts=self.store.get_meta("last_ts"),
                )
                if code == 200:
                    self.config_cache[wid] = self.client.get(f"/workflows/{wid}/config")
                    self.store.commit()
                    return True
                # fall through to approval path if auto-apply rejected

        affected = max(self.store.lead_count(wid), 0)
        pid = f"aprreq-{uuid.uuid4()}"
        body = {
            "proposal_id": pid,
            "workflow_id": wid,
            "base_version": cur["version"],
            "proposed_config": new_cfg,
            "change_summary": summary[:200],
            "diff": diff,
            "affected_leads": int(affected),
            "dry_run": dry_run,
            "impact_score": float(max(0.0, min(1.0, impact))),
            "rationale": rationale,
        }
        if evidence:
            body["evidence"] = evidence
        code, resp = self.client.post("/approvals", json=body)
        self.requests_today.setdefault(wid, []).append(sim_minute)
        if code == 200:
            aid = resp["approval_id"]
            self.pending_apply[wid] = aid
            self.store.save_proposal(
                pid, wid, "pending", kind, {"diff": diff, "dry_run": dry_run, "summary": summary},
                approval_id=aid, base_version=cur["version"],
            )
            self.store.audit(
                "proposal_submitted",
                summary,
                {"approval_id": aid, "diff": diff, "impact": impact},
                workflow_id=wid,
                sim_ts=self.store.get_meta("last_ts"),
            )
            self.store.commit()
            return True
        self.store.save_proposal(
            pid, wid, f"error_{code}", kind, {"response": resp, "diff": diff},
            base_version=cur["version"],
        )
        self.store.audit(
            "proposal_failed",
            f"{wid} {kind} failed {code}",
            {"response": resp},
            workflow_id=wid,
            sim_ts=self.store.get_meta("last_ts"),
        )
        self.store.commit()
        return False

    def _apply_pending(self, sim_minute: int) -> None:
        for wid, aid in list(self.pending_apply.items()):
            ap = self.client.get_json(f"/approvals/{aid}")
            if not ap:
                self.pending_apply.pop(wid, None)
                continue
            status = ap["status"]
            if status == "approved":
                code, body = self.client.post(f"/workflows/{wid}/apply", json={"approval_id": aid})
                if code == 200:
                    self.store.audit(
                        "applied",
                        f"applied {aid} on {wid} → v{body.get('version')}",
                        {"body": body, "approval": ap},
                        workflow_id=wid,
                        sim_ts=self.store.get_meta("last_ts"),
                    )
                    # Mark matching proposal
                    for p in self.store.open_proposals():
                        if p.get("approval_id") == aid:
                            self.store.update_proposal_status(p["proposal_id"], "applied", aid)
                    self.config_cache[wid] = self.client.get(f"/workflows/{wid}/config")
                    self.pending_apply.pop(wid, None)
                elif code == 409 and body.get("error") == "stale_base_version":
                    self.store.audit(
                        "stale_base",
                        f"stale apply for {aid}; will re-propose",
                        {"body": body},
                        workflow_id=wid,
                        sim_ts=self.store.get_meta("last_ts"),
                    )
                    self.pending_apply.pop(wid, None)
                    for p in self.store.open_proposals():
                        if p.get("approval_id") == aid:
                            self.store.update_proposal_status(p["proposal_id"], "stale", aid)
                elif code == 410:
                    self.pending_apply.pop(wid, None)
                    for p in self.store.open_proposals():
                        if p.get("approval_id") == aid:
                            self.store.update_proposal_status(p["proposal_id"], "lapsed", aid)
                # else still approved — retry next tick
            elif status in ("rejected", "expired", "lapsed", "applied"):
                for p in self.store.open_proposals():
                    if p.get("approval_id") == aid:
                        self.store.update_proposal_status(p["proposal_id"], status, aid)
                self.pending_apply.pop(wid, None)
                if status in ("rejected", "expired"):
                    self.store.audit(
                        "proposal_closed",
                        f"{aid} {status}",
                        {"reasons": ap.get("reasons"), "approval": ap},
                        workflow_id=wid,
                        sim_ts=self.store.get_meta("last_ts"),
                    )
        self.store.commit()

    # ------------------------------------------------------------------ policies
    def ensure_cohorts(self, wid: str, sim_minute: int) -> None:
        cur = self.config_cache[wid]
        cfg = deep_copy(cur["config"])
        desired = build_cohorts(self.workflows[wid])
        if cfg.get("cohorts") == desired:
            return
        # Only set cohorts early; leave allocations default until explore.
        cfg["cohorts"] = desired
        # Ensure each cohort has at least empty alloc inheritance via default.
        cfg.setdefault("allocations", {}).setdefault("default", cur["config"]["allocations"]["default"])
        self._submit_approval(
            wid,
            cfg,
            kind="cohorts",
            summary=f"Define shared cohorts for {wid}: {', '.join(c['id'] for c in desired)}",
            rationale=(
                "One shared cohort object for detection, experiments and audit. "
                "Coarse slices (language / age) to keep sample sizes viable."
            ),
            diff={"cohorts": desired},
            impact=0.15,
            dry_run={"note": "structural only; no traffic shift yet"},
            sim_minute=sim_minute,
        )

    def maybe_explore_and_promote(self, wid: str, sim_minute: int, day: int) -> None:
        wf = self.workflows[wid]
        primary = primary_kpi(wf)
        guard = guardrail_kpi(wf)
        cur = self.config_cache[wid]
        cfg = deep_copy(cur["config"])
        opts = wf["options"]
        gov = wf["governance"]

        # One batched change per request when possible.
        diff: dict[str, Any] = {}
        changed = False
        rationales = []

        for dim in ("welcome_message", "voice"):
            exp = self.store.get_experiment(wid, dim) or {
                "phase": "explore",
                "started_day": day,
                "promoted_value": None,
                "exposure_days": 0,
            }
            phase = exp.get("phase") or "explore"
            options = list(opts[dim])
            default_alloc = (cfg.get("allocations") or {}).get("default") or {}
            current_dist = default_alloc.get(dim) or {}

            if phase == "explore":
                # Ensure exploration is running (uniform across options) — once.
                target = uniform(options)
                already = self.store.get_meta(f"explore_started:{wid}:{dim}", False)
                if current_dist != target and not already:
                    cfg.setdefault("allocations", {}).setdefault("default", {})[dim] = target
                    for c in cfg.get("cohorts") or []:
                        cfg["allocations"].setdefault(c["id"], {})[dim] = target
                    diff[f"allocations.*.{dim}"] = target
                    changed = True
                    rationales.append(f"explore {dim} uniformly across {options}")
                    self.store.set_experiment(wid, dim, phase="explore", started_day=day)
                    self.store.set_meta(f"explore_started:{wid}:{dim}", True)

                # Try to promote if enough evidence on ALL cohort.
                min_n = 150 if primary == "engagement_rate" else 250
                rows = self.store.get_stats(wid, dim, window="all", cohort_id="ALL")
                # During uniform explore, baseline is the historical default (first option / W1 V1).
                baseline = options[0]
                winner, meta = choose_winner(
                    rows, primary=primary, guardrail=guard, baseline_value=baseline, min_n=min_n
                )
                # Only one promotion per dim — avoid flip-flops.
                if winner and not self.store.get_meta(f"promoted_once:{wid}:{dim}", False):
                    # Cohort-specific winners where evidence is strong.
                    allocs = cfg.setdefault("allocations", {})
                    allocs.setdefault("default", {})[dim] = explore_or_blend(options, winner, 0.15)
                    for c in cfg.get("cohorts") or []:
                        crows = self.store.get_stats(wid, dim, window="all", cohort_id=c["id"])
                        cw, _ = choose_winner(
                            crows, primary=primary, guardrail=guard, baseline_value=winner, min_n=max(80, min_n // 2)
                        )
                        pick = cw or winner
                        allocs.setdefault(c["id"], {})[dim] = explore_or_blend(options, pick, 0.15)
                    diff[f"promote.{dim}"] = {"winner": winner, "meta": meta}
                    changed = True
                    rationales.append(f"promote {dim}={winner} (z={meta.get('z')})")
                    self.store.set_experiment(
                        wid, dim, phase="exploit", started_day=day, promoted_value=winner, exposure_days=0
                    )
                    self.store.set_meta(f"promoted_once:{wid}:{dim}", True)
                    self.store.set_meta(f"explore_started:{wid}:{dim}", False)
                    self.store.audit(
                        "winner_selected",
                        f"{wid} {dim}={winner}",
                        meta,
                        workflow_id=wid,
                        sim_ts=self.store.get_meta("last_ts"),
                    )

            elif phase == "exploit":
                promoted = exp.get("promoted_value")
                exposure = int(exp.get("exposure_days") or 0)
                # Decay detection: compare recent vs all-time alternative arms.
                recent = self.store.get_stats(wid, dim, window="recent", cohort_id="ALL")
                all_rows = self.store.get_stats(wid, dim, window="all", cohort_id="ALL")
                decayed = False
                if promoted and exposure >= 2:
                    decayed = detect_decay(recent, promoted, primary, min_n=60, drop=0.025)
                    if not decayed and recent:
                        # Also: if promoted's recent rate is clearly below its own historical.
                        by_r = {r["value"]: r for r in recent}
                        by_a = {r["value"]: r for r in all_rows}
                        if promoted in by_r and promoted in by_a:
                            rr, ar = by_r[promoted], by_a[promoted]
                            if primary == "engagement_rate":
                                rn, rs = int(rr["connected"]), int(rr["engaged"])
                                an, as_ = int(ar["connected"]), int(ar["engaged"])
                            else:
                                rn, rs = int(rr["dials"]), int(rr["connected"])
                                an, as_ = int(ar["dials"]), int(ar["connected"])
                            if rn >= 80 and an >= 150 and rate(rs, rn) + 0.04 < rate(as_, an):
                                decayed = True
                if promoted and decayed and not self.store.get_meta(f"decayed_once:{wid}:{dim}", False):
                    # One soft rollback: shift toward next-best arm, then lock the dim.
                    alt_winner, _ = choose_winner(
                        all_rows,
                        primary=primary,
                        guardrail=guard,
                        baseline_value=promoted,
                        min_n=100,
                    )
                    fallback = alt_winner if alt_winner and alt_winner != promoted else options[0]
                    target = explore_or_blend(options, fallback, 0.20)
                    cfg.setdefault("allocations", {}).setdefault("default", {})[dim] = target
                    for c in cfg.get("cohorts") or []:
                        cfg["allocations"].setdefault(c["id"], {})[dim] = target
                    diff[f"rollback.{dim}"] = {"from": promoted, "to": target}
                    changed = True
                    rationales.append(f"decay rollback on {dim}={promoted} → {fallback}")
                    self.store.set_experiment(
                        wid,
                        dim,
                        phase="exploit",
                        promoted_value=fallback,
                        exposure_days=0,
                        started_day=day,
                    )
                    self.store.set_meta(f"decayed_once:{wid}:{dim}", True)
                    self.store.set_meta(f"promoted_once:{wid}:{dim}", True)
                    self.store.audit(
                        "decay_rollback",
                        f"{wid} rolled back {dim}={promoted} → {fallback}",
                        {"recent": recent},
                        workflow_id=wid,
                        sim_ts=self.store.get_meta("last_ts"),
                    )
                else:
                    # Stay put while exploiting — avoid daily allocation churn.
                    self.store.set_experiment(wid, dim, exposure_days=exposure + 1)

        if changed and rationales:
            # Impact from the strongest promote if any.
            impact = 0.35
            dry = {"batched": True, "parts": rationales}
            for k, v in diff.items():
                if k.startswith("promote."):
                    dim = k.split(".", 1)[1]
                    w = v.get("winner")
                    if w:
                        impact, dry = self._estimate_impact(wid, dim, w, primary)
                        dry["batched_parts"] = rationales
            self._submit_approval(
                wid,
                cfg,
                kind="experiment",
                summary="; ".join(rationales)[:200],
                rationale="Statistical winner selection on primary KPI with guardrail check; "
                "LLM not used for winner decisions. " + " | ".join(rationales),
                diff=diff,
                impact=impact,
                dry_run=dry,
                sim_minute=sim_minute,
            )

    def maybe_caller_id(self, wid: str, sim_minute: int) -> None:
        wf = self.workflows[wid]
        opts = list(wf["options"]["caller_id"])
        # Prefer recent; fall back to all for cold start / history bootstrap signal.
        rows = self.store.get_stats(wid, "caller_id", window="recent", cohort_id="ALL")
        if sum(int(r["dials"]) for r in rows) < 200:
            rows = self.store.get_stats(wid, "caller_id", window="all", cohort_id="ALL")
        health = did_health_rows(rows)
        if not health:
            return
        if max(h["dials"] for h in health) < 100:
            return
        # Cool-down after a DID change to prevent oscillation (shorter if gap is huge).
        last_day = self.store.get_meta(f"did_change_day:{wid}")
        ts = self.store.get_meta("last_ts")
        day = day_index(ts) if ts else 0
        best = health[0]
        # Conservative: ≥120 dials and ≥7pp absolute pickup gap vs best.
        unhealthy = [
            h
            for h in health
            if h["dials"] >= 120
            and h["value"] != best["value"]
            and h["pickup"] + 0.07 < best["pickup"]
            and h["wilson"] < best["wilson"] - 0.05
        ]
        big_gap = bool(unhealthy) and (best["pickup"] - min(h["pickup"] for h in unhealthy)) >= 0.12
        cooldown = 1 if big_gap else 2
        if last_day is not None and day - int(last_day) < cooldown:
            return
        cur = self.config_cache[wid]
        cfg = deep_copy(cur["config"])
        default = cfg.setdefault("allocations", {}).setdefault("default", {})
        current = default.get("caller_id") or {}

        if unhealthy:
            bad = {h["value"] for h in unhealthy}
            good = [o for o in opts if o not in bad]
            if not good:
                good = [best["value"]]
            # Explore remaining DIDs equally; zero weight on unhealthy.
            target = {g: 1.0 for g in good}
            for b in bad:
                target[b] = 0.0
            # Drop zero entries to keep config clean
            target = {k: v for k, v in target.items() if v > 0}
            if current != target:
                default["caller_id"] = target
                for c in cfg.get("cohorts") or []:
                    cfg.setdefault("allocations", {}).setdefault(c["id"], {})["caller_id"] = target
                worst_pickup = min(h["pickup"] for h in unhealthy)
                impact = min(1.0, max(0.2, (best["pickup"] - worst_pickup) / 0.1))
                if self._submit_approval(
                    wid,
                    cfg,
                    kind="caller_id",
                    summary=f"Rotate off unhealthy caller IDs: {sorted(bad)}; keep {sorted(target)}",
                    rationale=(
                        f"Population pickup: best {best['value']}="
                        f"{best['pickup']:.3f}, unhealthy={{{', '.join(f'{h['value']}={h['pickup']:.3f}' for h in unhealthy)}}}. "
                        "Act on thresholds over ≥150 dials and ≥8pp gap, not single calls."
                    ),
                    diff={"caller_id": target, "unhealthy": list(bad)},
                    impact=impact,
                    dry_run={"health": health, "method": "pickup_wilson"},
                    sim_minute=sim_minute,
                ):
                    self.store.set_meta(f"did_change_day:{wid}", day)
        else:
            # Gentle one-shot exploration across DIDs if stuck on a single ID.
            explored = self.store.get_meta(f"did_explored:{wid}", False)
            if len(current) == 1 and len(opts) > 1 and not explored and day >= 1:
                target = uniform(opts)
                default["caller_id"] = target
                if self._submit_approval(
                    wid,
                    cfg,
                    kind="caller_id_explore",
                    summary="Explore caller IDs uniformly to measure health",
                    rationale="Need coverage across DIDs before declaring winners.",
                    diff={"caller_id": target},
                    impact=0.1,
                    dry_run={"health": health},
                    sim_minute=sim_minute,
                ):
                    self.store.set_meta(f"did_explored:{wid}", True)
                    self.store.set_meta(f"did_change_day:{wid}", day)

    def maybe_hallucination(self, wid: str, sim_minute: int, day: int) -> None:
        wf = self.workflows[wid]
        gov = wf["governance"]
        # Detection uses the *recent* window only — never fall back to all-time,
        # or a quiet day will inherit history and look like a spike after a reset.
        rows = self.store.get_stats(wid, "prompt_version", window="recent", cohort_id="ALL")
        by = {r["value"]: r for r in rows}
        cur = self.config_cache[wid]
        cur_pv = cur["config"].get("prompt_version")
        if cur_pv not in by:
            return
        r = by[cur_pv]
        connected = int(r["connected"])
        halluc = int(r["halluc"])
        if connected < 100:
            return
        rate_h = halluc / connected
        inc = self.store.get_incident(wid)
        cooldown = inc.get("cooldown_until_day")
        if cooldown is not None and day < int(cooldown):
            return

        # Baseline = best prior prompt's lifetime rate (not the suspect itself).
        all_rows = self.store.get_stats(wid, "prompt_version", window="all", cohort_id="ALL")
        prior = [
            x
            for x in all_rows
            if x["value"] != cur_pv and int(x["connected"]) >= 100
        ]
        if prior:
            base_rate = min(int(x["halluc"]) / max(int(x["connected"]), 1) for x in prior)
        else:
            base_rate = 0.006

        suspect = self.store.get_meta(f"prompt_suspect:{wid}")
        forced = bool(inc.get("halluc_active")) and suspect == cur_pv

        # Absolute floor catches bad new prompts; relative catch catches regressions.
        # Non-forced changes are expensive and easy to get wrong — require a clear spike.
        SPIKE = 0.03 if forced else max(0.05, base_rate + 0.035)
        if rate_h < SPIKE and not (forced and connected >= 60 and rate_h >= 0.02):
            if inc.get("halluc_active") and not forced and rate_h < max(0.02, base_rate + 0.01):
                self.store.set_incident(
                    wid,
                    halluc_active=0,
                    candidate_prompt=None,
                    validation_run_id=None,
                )
            if not forced:
                return
            if rate_h < 0.02 and connected >= 150:
                # Supervisor prompt looks fine — clear the watch.
                self.store.set_incident(wid, halluc_active=0)
                self.store.set_meta(f"prompt_suspect:{wid}", None)
                return

        self.store.set_incident(wid, halluc_active=1)
        self.store.audit(
            "halluc_detected",
            f"{wid} halluc_rate={rate_h:.3f} on {connected} connected",
            {"row": dict(r), "threshold": SPIKE, "base_rate": base_rate, "forced": forced},
            workflow_id=wid,
            sim_ts=self.store.get_meta("last_ts"),
        )

        # If we already have a validated candidate, propose it (retry until accepted).
        if inc.get("candidate_prompt") and inc.get("validation_run_id"):
            cand = inc["candidate_prompt"]
            if cand == cur_pv:
                self.store.set_meta(f"prompt_suspect:{wid}", None)
                self.store.set_incident(wid, halluc_active=0)
                return
            new_cfg = deep_copy(cur["config"])
            new_cfg["prompt_version"] = cand
            impact = min(1.0, max(rate_h, 0.05) / 0.05)
            ok = self._submit_approval(
                wid,
                new_cfg,
                kind="prompt_fix",
                summary=f"Replace {cur_pv} after hallucination spike ({rate_h:.1%}) with validated {cand}",
                rationale=(
                    f"Connected-call hallucination flag rate {rate_h:.3f} over {connected} calls. "
                    f"Validation run {inc['validation_run_id']} attached."
                ),
                diff={"prompt_version": {"from": cur_pv, "to": cand}},
                impact=impact,
                dry_run={"halluc_rate": rate_h, "connected": connected},
                evidence={"validation_run_id": inc["validation_run_id"]},
                sim_minute=sim_minute,
                urgent=True,
            )
            if ok:
                self.store.set_incident(wid, last_action_day=day)
            return

        # Ask LLM for suggestions, then validate each until one passes.
        state = self.client.get("/sim/state")
        credits = state.get("credits_remaining", 0)
        if credits < 10:
            self.store.audit("credits_low", f"only {credits} credits; deferring prompt fix", {}, workflow_id=wid)
            return

        code, sug = self.client.post(
            "/llm/suggest-fixes",
            json={
                "workflow_id": wid,
                "problem": (
                    f"Hallucination flag rate {rate_h:.3f} on prompt {cur_pv}; "
                    "need a safer grounded script that still keeps people on the call "
                    "(avoid near-verbatim strict scripts and avoid more aggressive/persuasive language)"
                ),
            },
        )
        if code != 200:
            self.store.audit("llm_failed", f"suggest-fixes {code}", {"body": sug}, workflow_id=wid)
            return

        suggestions = sug.get("suggestions") or []
        # Prefer grounded; avoid persuasive/urgency; deprioritise strict verbatim scripts.
        def rank(s):
            text = (s.get("summary") or "").lower()
            score = 0
            if "ground" in text:
                score += 5
            if "strict" in text or "verbatim" in text:
                score -= 1
            if "persuasive" in text or "freer" in text or "urgency" in text or "aggressive" in text:
                score -= 5
            return -score

        suggestions = sorted(suggestions, key=rank)
        min_pass = gov.get("min_validation_pass_rate", 0.97)
        for s in suggestions:
            pv = s["prompt_version"]
            st = self.client.get("/sim/state")
            if st.get("credits_remaining", 0) < 5:
                break
            vcode, vbody = self.client.post(
                "/validation/runs",
                json={"workflow_id": wid, "prompt_version": pv, "n": 200},
            )
            self.store.audit(
                "validation",
                f"{wid} validate {pv} → {vbody}",
                {"response": vbody},
                workflow_id=wid,
                sim_ts=self.store.get_meta("last_ts"),
            )
            if vcode != 200:
                continue
            if vbody.get("pass_rate", 0) >= min_pass:
                self.store.set_incident(
                    wid,
                    candidate_prompt=pv,
                    validation_run_id=vbody["validation_run_id"],
                    last_action_day=day,
                    halluc_active=1,
                )
                new_cfg = deep_copy(cur["config"])
                new_cfg["prompt_version"] = pv
                ok = self._submit_approval(
                    wid,
                    new_cfg,
                    kind="prompt_fix",
                    summary=f"Swap to {pv} after hallucination spike on {cur_pv}",
                    rationale=(
                        f"Detector rate {rate_h:.3f} over {connected} connected calls. "
                        f"Validation pass_rate={vbody['pass_rate']} (n={vbody['n']}). "
                        f"Summary: {s.get('summary')}"
                    ),
                    diff={"prompt_version": {"from": cur_pv, "to": pv}},
                    impact=min(1.0, max(rate_h, 0.05) / 0.05),
                    dry_run={"halluc_rate": rate_h, "validation": vbody},
                    evidence={"validation_run_id": vbody["validation_run_id"]},
                    sim_minute=sim_minute,
                    urgent=True,
                )
                self.store.commit()
                return

        # Nothing passed — short cool-down, then retry next day.
        self.store.set_incident(wid, cooldown_until_day=day + 1, last_action_day=day)
        self.store.commit()

    def maybe_schedule(self, wid: str, sim_minute: int) -> None:
        """Lightweight hour-weight tuning from historical pickup (auto-apply friendly)."""
        if self.store.get_meta(f"schedule_tuned:{wid}", False):
            return
        wf = self.workflows[wid]
        lo, hi = wf["dialling_window"]
        hours = self.store.get_hour_stats(wid)
        if sum(h["dials"] for h in hours) < 500:
            return
        weights = {}
        for h in hours:
            hr = int(h["hour"])
            if not (lo <= hr < hi):
                continue
            # Weight proportional to wilson lower of pickup; floor so no hour is zeroed accidentally.
            w = max(0.35, wilson_lower(int(h["connected"]), int(h["dials"])))
            weights[str(hr)] = round(w, 3)
        if not weights:
            return
        for hr in range(lo, hi):
            weights.setdefault(str(hr), 0.35)

        cur = self.config_cache[wid]
        cfg = deep_copy(cur["config"])
        sched = cfg.setdefault("schedule", {})
        hw = sched.setdefault("hour_weights", {})
        if hw.get("default") == weights:
            self.store.set_meta(f"schedule_tuned:{wid}", True)
            return
        hw["default"] = weights

        prefs = self.store.best_hours_for_people(wid, min_dials=5, limit=2000)
        if prefs:
            lph = sched.setdefault("lead_preferred_hour", {})
            if len(lph) < 500:
                for pid, hr in list(prefs.items())[:500]:
                    if lo <= hr < hi:
                        lph[pid] = hr

        if self._submit_approval(
            wid,
            cfg,
            kind="schedule",
            summary=f"Tune call-time hour weights for {wid} from historical pickup",
            rationale="Hour weights proportional to Wilson lower-bound pickup; never leave window hours at 0.",
            diff={"hour_weights.default": weights, "preferred_hours_added": min(len(prefs), 500)},
            impact=0.25,
            dry_run={"hour_stats": hours[:24]},
            sim_minute=sim_minute,
        ):
            self.store.set_meta(f"schedule_tuned:{wid}", True)

    # ------------------------------------------------------------------ main loop
    def _sim_minute(self, ts: str) -> int:
        t = parse_ts(ts).astimezone(IST)
        base = datetime(2027, 1, 4, tzinfo=IST)
        return int((t - base).total_seconds() // 60)

    def run(self) -> None:
        self.bootstrap()
        while True:
            code, st = self.client.post("/sim/advance", json={"minutes": self.tick})
            if code == 409 and (st.get("error") == "finished" or st.get("detail") == "finished"):
                break
            if code != 200:
                # finished may also come as 200 with finished true
                if isinstance(st, dict) and st.get("finished"):
                    break
                self.store.audit("advance_error", f"advance failed {code}", {"body": st})
                self.store.commit()
                if code == 409:
                    break
                continue

            ts = st["ts"]
            self.store.set_meta("last_ts", ts)
            day = day_index(ts)
            sim_min = self._sim_minute(ts)

            # Reset recent stats each sim day at midnight-ish.
            if self.day_seen_for_recent_reset != day:
                if self.day_seen_for_recent_reset is not None:
                    self.store.reset_recent()
                self.day_seen_for_recent_reset = day

            for ch in st.get("new_chunks") or []:
                self._ingest_chunk(ch["chunk"])

            self._apply_pending(sim_min)

            # Decision cadence: every tick after D0 morning, but heavy actions near midday.
            h = hour_of(ts)
            for wid in self.workflows:
                # Refresh config cache lightly
                if wid not in self.config_cache:
                    self.config_cache[wid] = self.client.get(f"/workflows/{wid}/config")

                # Incidents first — never blocked by an unrelated pending approval.
                if 10 <= h < 19:
                    self.maybe_hallucination(wid, sim_min, day)

                if wid in self.pending_apply:
                    continue

                # While a prompt incident is open, hold other changes so the fix can clear governance.
                if self.store.get_incident(wid).get("halluc_active"):
                    continue

                if day >= 0 and h == 11:
                    self.ensure_cohorts(wid, sim_min)
                if wid in self.pending_apply:
                    continue

                if day >= 0 and h in (11, 15, 18):
                    self.maybe_caller_id(wid, sim_min)
                if wid in self.pending_apply:
                    continue

                if day >= 1 and h in (12, 16):
                    self.maybe_explore_and_promote(wid, sim_min, day)
                if wid in self.pending_apply:
                    continue

                if day >= 2 and h == 13:
                    self.maybe_schedule(wid, sim_min)

            self.store.checkpoint(last_ts=ts, last_day=day)
            self.store.commit()

            if st.get("finished"):
                break

        self.store.audit("finished", "scenario complete", {"state": self.client.get("/sim/state")})
        self.store.commit()
        self.client.close()
        self.store.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Formi outcome-first controller")
    ap.add_argument("--url", default=os.environ.get("SIM_URL", "http://127.0.0.1:8700"))
    ap.add_argument(
        "--data-dir",
        default=os.environ.get("CONTROLLER_DATA", "controller_data"),
        help="Durable state directory (survives crash/restart)",
    )
    ap.add_argument("--tick", type=int, default=60, help="Sim minutes per advance")
    args = ap.parse_args(argv)
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    Controller(args.url, data, tick_minutes=args.tick).run()


if __name__ == "__main__":
    main()
