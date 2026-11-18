"""Wire-shape builders shared by REST and WebSocket code (CONTRACT §2.2 / §4)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cardiosense.models import Alert, Beat, MonitoringSession, RhythmWindow


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def alert_to_dict(a: Alert, *, with_evidence: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": a.id,
        "patient_id": a.patient_id,
        "session_id": a.session_id,
        "rule_id": a.rule_id,
        "tier": a.tier,
        "title": a.title,
        "detail": a.detail,
        "state": a.state,
        "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at),
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_by_id": a.acknowledged_by_id,
        "acknowledged_at": iso(a.acknowledged_at),
        "note": a.note,
        "occurrences": a.occurrences,
        "last_seen_at": iso(a.last_seen_at),
        "is_demo": a.is_demo,
        "rule_version": a.rule_version,
        "model_version": a.model_version,
        "history": a.history or [],
    }
    if with_evidence:
        out["evidence"] = a.evidence or {}
    return out


def beat_to_dict(b: Beat) -> dict[str, Any]:
    return {
        "sample": b.sample,
        "time_s": b.time_s,
        "label": b.label,
        "confidence": b.confidence,
        "probs": b.probs,
        "model_version": b.model_version,
        "truth": b.truth,
    }


def window_to_dict(w: RhythmWindow) -> dict[str, Any]:
    return {
        "id": w.id,
        "session_id": w.session_id,
        "t": iso(w.t),
        "start_s": w.start_s,
        "end_s": w.end_s,
        "hr_bpm": w.hr_bpm,
        "findings": w.findings or [],
        "s_burden_pct": w.s_burden_pct,
        "v_burden_pct": w.v_burden_pct,
        "sqi": w.sqi,
        "sdnn_ms": w.sdnn_ms,
        "rmssd_ms": w.rmssd_ms,
        "beat_counts": w.beat_counts or {},
        "rule_version": w.rule_version,
        "risk": w.risk or {"score": w.risk_score, "tier": w.risk_tier, "factors": []},
    }


def session_to_dict(s: MonitoringSession, *, active: bool | None = None) -> dict[str, Any]:
    is_active = s.ended_at is None if active is None else active
    duration = None
    if s.fs:
        duration = round(s.last_sample / s.fs, 3)
    return {
        "id": s.id,
        "session_id": s.id,
        "patient_id": s.patient_id,
        "device_id": s.device_id,
        "source": s.source,
        "record": s.record,
        "dataset": s.dataset,
        "fs": s.fs,
        "firmware": s.firmware,
        "speed": s.speed,
        "started_at": iso(s.started_at),
        "ended_at": iso(s.ended_at),
        "active": is_active,
        "duration_s": duration,
        "model_version": s.model_version,
        "rule_version": s.rule_version,
        "rule_config_version": s.rule_config_version,
        "model_loaded": s.model_loaded,
        "is_demo": s.is_demo,
        "stats": s.stats or {},
    }


def session_message(s: dict[str, Any]) -> dict[str, Any]:
    """The ``session`` live message (CONTRACT §2.2) from a ``session_to_dict`` result."""
    return {
        "type": "session",
        "session_id": s["id"],
        "patient_id": s["patient_id"],
        "device_id": s["device_id"],
        "fs": s["fs"],
        "source": s["source"],
        "record": s["record"],
        "dataset": s["dataset"],
        "started_at": s["started_at"],
        "ended_at": s["ended_at"],
        "model_version": s["model_version"],
        "rule_version": s["rule_version"],
        "model_loaded": s["model_loaded"],
        "is_demo": s["is_demo"],
        "active": s["active"],
    }
