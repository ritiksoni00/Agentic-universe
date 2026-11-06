"""Statistical helpers for experiment decisions (no LLM)."""
from __future__ import annotations

import math


def wilson_lower(successes: int, n: int, z: float = 1.64) -> float:
    """Lower bound of Wilson score interval (approx one-sided 95% when z=1.64)."""
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def rate(successes: int, n: int) -> float:
    return successes / n if n else 0.0


def two_proportion_z(s1: int, n1: int, s2: int, n2: int) -> float | None:
    """Two-sided z for difference of proportions. None if underpowered."""
    if n1 < 30 or n2 < 30:
        return None
    p1, p2 = s1 / n1, s2 / n2
    p = (s1 + s2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se < 1e-12:
        return 0.0
    return (p1 - p2) / se


def choose_winner(
    rows: list[dict],
    *,
    primary: str,
    guardrail: str | None,
    baseline_value: str | None,
    min_n: int,
    z_thresh: float = 1.64,
) -> tuple[str | None, dict]:
    """Pick a statistically better variant on the primary KPI without hurting the guardrail.

    rows: [{value, dials, connected, engaged, halluc}]
    primary/guardrail: 'engagement_rate' | 'pickup_rate'
    """
    meta: dict = {"candidates": [], "reason": None}
    scored = []
    for r in rows:
        dials = int(r["dials"])
        connected = int(r["connected"])
        engaged = int(r["engaged"])
        if primary == "engagement_rate":
            n, s = connected, engaged
        else:
            n, s = dials, connected
        if n < min_n:
            continue
        g_n = g_s = None
        if guardrail == "pickup_rate":
            g_n, g_s = dials, connected
        elif guardrail == "engagement_rate":
            g_n, g_s = connected, engaged
        scored.append(
            {
                "value": r["value"],
                "n": n,
                "s": s,
                "rate": rate(s, n),
                "wilson": wilson_lower(s, n),
                "g_n": g_n,
                "g_s": g_s,
                "g_rate": rate(g_s, g_n) if g_n else None,
            }
        )
    meta["candidates"] = scored
    if len(scored) < 2:
        meta["reason"] = "insufficient_arms"
        return None, meta

    scored.sort(key=lambda x: (x["wilson"], x["rate"], x["n"]), reverse=True)
    best = scored[0]
    base = next((x for x in scored if x["value"] == baseline_value), scored[-1])
    if best["value"] == base["value"]:
        meta["reason"] = "baseline_still_best"
        return None, meta

    z = two_proportion_z(best["s"], best["n"], base["s"], base["n"])
    meta["z"] = z
    if z is None or z < z_thresh:
        meta["reason"] = "not_significant"
        return None, meta

    # Guardrail: do not allow >2pp absolute drop vs baseline on the other KPI.
    if best["g_rate"] is not None and base["g_rate"] is not None:
        if best["g_rate"] + 1e-9 < base["g_rate"] - 0.02:
            meta["reason"] = "guardrail_fail"
            return None, meta

    meta["reason"] = "promote"
    return best["value"], meta


def detect_decay(
    recent_rows: list[dict],
    promoted_value: str,
    primary: str,
    min_n: int = 80,
    drop: float = 0.03,
) -> bool:
    """True if the promoted variant's recent rate is clearly below alternatives."""
    by = {r["value"]: r for r in recent_rows}
    if promoted_value not in by:
        return False
    cur = by[promoted_value]
    if primary == "engagement_rate":
        n, s = int(cur["connected"]), int(cur["engaged"])
    else:
        n, s = int(cur["dials"]), int(cur["connected"])
    if n < min_n:
        return False
    cur_rate = rate(s, n)
    for val, r in by.items():
        if val == promoted_value:
            continue
        if primary == "engagement_rate":
            n2, s2 = int(r["connected"]), int(r["engaged"])
        else:
            n2, s2 = int(r["dials"]), int(r["connected"])
        if n2 < min_n:
            continue
        if rate(s2, n2) > cur_rate + drop:
            z = two_proportion_z(s2, n2, s, n)
            if z is not None and z >= 1.64:
                return True
    return False


def did_health_rows(rows: list[dict]) -> list[dict]:
    """Caller-ID health proxy = pickup rate, with wilson lower bound."""
    out = []
    for r in rows:
        dials = int(r["dials"])
        connected = int(r["connected"])
        out.append(
            {
                "value": r["value"],
                "dials": dials,
                "pickup": rate(connected, dials),
                "wilson": wilson_lower(connected, dials),
            }
        )
    return sorted(out, key=lambda x: x["wilson"], reverse=True)
