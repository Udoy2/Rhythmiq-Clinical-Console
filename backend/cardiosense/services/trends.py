"""Trend aggregation over stored rhythm windows (FR-17): time buckets plus explicit gaps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.config import TIER_RANK
from cardiosense.services.serialize import iso

# range -> (span, bucket seconds)
RANGES: dict[str, tuple[timedelta, int]] = {
    "24h": (timedelta(hours=24), 300),
    "7d": (timedelta(days=7), 3600),
    "14d": (timedelta(days=14), 3 * 3600),
    "30d": (timedelta(days=30), 6 * 3600),
    "90d": (timedelta(days=90), 24 * 3600),
}

_BUCKET_SQL = text(
    """
    SELECT date_bin(make_interval(secs => :bucket), t, :origin) AS b,
           avg(hr_bpm) AS hr_bpm, avg(sdnn_ms) AS sdnn_ms, avg(rmssd_ms) AS rmssd_ms,
           avg(s_burden_pct) AS s_burden_pct, avg(v_burden_pct) AS v_burden_pct,
           max(risk_score) AS risk_max, avg(risk_score) AS risk_mean, avg(sqi) AS sqi, count(*) AS n
    FROM rhythm_windows
    WHERE patient_id = :pid AND t >= :start AND t < :end
    GROUP BY b ORDER BY b
    """
)

_FINDING_SQL = text(
    """
    SELECT date_bin(make_interval(secs => :bucket), w.t, :origin) AS b,
           f->>'rule_id' AS rule_id, f->>'severity' AS severity, max(f->>'label') AS label, count(*) AS n
    FROM rhythm_windows w CROSS JOIN LATERAL jsonb_array_elements(w.findings) AS f
    WHERE w.patient_id = :pid AND w.t >= :start AND w.t < :end AND jsonb_array_length(w.findings) > 0
    GROUP BY b, rule_id, severity
    """
)


def _r(value: Any, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def window_bounds(range_key: str, now: datetime | None = None) -> tuple[datetime, datetime, int]:
    span, bucket_s = RANGES[range_key]
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    end = datetime.fromtimestamp((epoch // bucket_s + 1) * bucket_s, tz=timezone.utc)
    return end - span, end, bucket_s


async def trend_buckets(
    db: AsyncSession, patient_id: str, start: datetime, end: datetime, bucket_s: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (buckets with data, gaps). A gap is a run of empty buckets: monitoring was not running."""
    params = {"bucket": bucket_s, "origin": start, "pid": patient_id, "start": start, "end": end}
    rows = (await db.execute(_BUCKET_SQL, params)).mappings().all()
    findings: dict[datetime, dict[str, dict[str, Any]]] = {}
    for f in (await db.execute(_FINDING_SQL, params)).mappings():
        per_rule = findings.setdefault(f["b"], {})
        cur = per_rule.get(f["rule_id"])
        if cur is None:
            per_rule[f["rule_id"]] = {"rule_id": f["rule_id"], "label": f["label"], "severity": f["severity"],
                                      "count": int(f["n"])}
        else:
            cur["count"] += int(f["n"])
            if TIER_RANK.get(f["severity"], 0) > TIER_RANK.get(cur["severity"], 0):
                cur["severity"] = f["severity"]

    buckets: list[dict[str, Any]] = []
    for r in rows:
        buckets.append({
            "t": iso(r["b"]),
            "hr_bpm": _r(r["hr_bpm"]),
            "sdnn_ms": _r(r["sdnn_ms"]),
            "rmssd_ms": _r(r["rmssd_ms"]),
            "s_burden_pct": _r(r["s_burden_pct"], 2),
            "v_burden_pct": _r(r["v_burden_pct"], 2),
            "risk_score": int(r["risk_max"]) if r["risk_max"] is not None else None,
            "risk_score_mean": _r(r["risk_mean"]),
            "sqi": _r(r["sqi"], 3),
            "n_windows": int(r["n"]),
            "findings": sorted(findings.get(r["b"], {}).values(),
                               key=lambda f: (-TIER_RANK.get(f["severity"], 0), -f["count"])),
        })

    gaps: list[dict[str, Any]] = []
    step = timedelta(seconds=bucket_s)
    cursor = start
    for r in rows:
        if r["b"] > cursor:
            gaps.append(_gap(cursor, r["b"]))
        cursor = r["b"] + step
    if cursor < end:
        gaps.append(_gap(cursor, end))
    return buckets, gaps


def _gap(start: datetime, end: datetime) -> dict[str, Any]:
    return {"start": iso(start), "end": iso(end), "from": iso(start), "to": iso(end),
            "duration_s": int((end - start).total_seconds())}


def summarise(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    """Window-weighted averages and finding totals over a list of buckets."""
    total = sum(b["n_windows"] for b in buckets)
    out: dict[str, Any] = {"n_windows": total, "monitored_s": total * 10}

    def wavg(key: str, digits: int = 1) -> float | None:
        pairs = [(b[key], b["n_windows"]) for b in buckets if b.get(key) is not None]
        weight = sum(n for _, n in pairs)
        return round(sum(v * n for v, n in pairs) / weight, digits) if weight else None

    for key, digits in (("hr_bpm", 1), ("sdnn_ms", 1), ("rmssd_ms", 1), ("s_burden_pct", 2), ("v_burden_pct", 2),
                        ("sqi", 3)):
        out[key] = wavg(key, digits)
    out["risk_score_mean"] = wavg("risk_score_mean")
    out["risk_score_max"] = max((b["risk_score"] for b in buckets if b["risk_score"] is not None), default=None)
    counts: dict[str, dict[str, Any]] = {}
    for b in buckets:
        for f in b["findings"]:
            cur = counts.setdefault(f["rule_id"], {**f, "count": 0})
            cur["count"] += f["count"]
            if TIER_RANK.get(f["severity"], 0) > TIER_RANK.get(cur["severity"], 0):
                cur["severity"] = f["severity"]
    out["findings"] = sorted(counts.values(), key=lambda f: (-TIER_RANK.get(f["severity"], 0), -f["count"]))
    return out
