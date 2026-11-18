"""Copilot context builder and retriever (BRD §8.6).

Collects the patient's own records — current/most recent session, the recent period versus a 14-day baseline,
alerts, notes, signal quality — and turns each into an evidence item with a stable citation id:
``S-`` session, ``W-`` rhythm window, ``T-`` trend, ``A-`` alert, ``N-`` note, ``Q-`` signal quality.
Evidence text never contains patient identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.db import utcnow
from cardiosense.models import Alert, ClinicalNote, MonitoringSession, Patient, RhythmWindow
from cardiosense.services.ingest import IngestManager
from cardiosense.services.serialize import iso
from cardiosense.services.trends import summarise, trend_buckets

BASELINE_DAYS = 14


@dataclass
class Evidence:
    id: str
    kind: str  # session | window | trend | alert | note | quality
    label: str
    time: str | None
    fact: str

    def citation(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "time": self.time}


@dataclass
class CopilotContext:
    patient_descriptor: str
    evidence: list[Evidence] = field(default_factory=list)
    # structured values for the rules-based template
    recent: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    recent_label: str = "the last 24 h"
    latest_window: RhythmWindow | None = None
    alerts: list[Alert] = field(default_factory=list)
    notes: list[ClinicalNote] = field(default_factory=list)
    session_id: str | None = None
    is_demo: bool = False

    def by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    def add(self, evidence: Evidence) -> Evidence:
        self.evidence.append(evidence)
        return evidence


def short(uuid_str: str) -> str:
    return uuid_str.replace("-", "")[:8]


def _when(dt: datetime | None) -> str:
    return dt.strftime("%d %b %H:%M UTC") if dt else "unknown time"


def _age_band(dob: date | None) -> str:
    if dob is None:
        return "age not recorded"
    age = (date.today() - dob).days // 365
    low = (age // 10) * 10
    return f"age {low}-{low + 9}"


def scrub(text: str, patient: Patient) -> str:
    """Remove patient identifiers from free text before it can reach a language model."""
    out = text
    for token in filter(None, [patient.mrn, *re.split(r"[^\w]+", patient.name or "")]):
        if len(token) >= 3 and not token.lower() in {"demo", "patient", "the", "mit", "bih", "record"}:
            out = re.sub(re.escape(token), "[redacted]", out, flags=re.I)
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[redacted]", out)
    out = re.sub(r"\+?\d[\d\s-]{7,}\d", "[redacted]", out)
    return out


def _findings_text(findings: list[dict[str, Any]], limit: int = 4) -> str:
    if not findings:
        return "no rule findings"
    return ", ".join(f"{f['label']} ×{f['count']}" if f.get("count") else str(f.get("label")) for f in findings[:limit])


def _period_fact(label: str, s: dict[str, Any]) -> str:
    minutes = s["monitored_s"] / 60
    parts = [f"{label}: {s['n_windows']} rhythm windows ({minutes:.0f} min monitored)"]
    if s.get("hr_bpm") is not None:
        parts.append(f"mean HR {s['hr_bpm']:.0f} bpm")
    if s.get("v_burden_pct") is not None:
        parts.append(f"mean V burden {s['v_burden_pct']:.1f}%")
    if s.get("s_burden_pct") is not None:
        parts.append(f"mean S burden {s['s_burden_pct']:.1f}%")
    if s.get("sdnn_ms") is not None:
        parts.append(f"SDNN {s['sdnn_ms']:.0f} ms, RMSSD {s.get('rmssd_ms') or 0:.0f} ms")
    if s.get("risk_score_max") is not None:
        parts.append(f"highest risk score {s['risk_score_max']}/100")
    parts.append(f"rule findings: {_findings_text(s.get('findings') or [])}")
    return "; ".join(parts) + "."


async def build_context(db: AsyncSession, ingest: IngestManager, patient: Patient) -> CopilotContext:
    now = utcnow()
    ctx = CopilotContext(
        patient_descriptor=f"adult patient, {_age_band(patient.dob)}, sex {patient.sex or 'not recorded'}"
        + (" (demo / replay data)" if patient.is_demo else ""),
        is_demo=patient.is_demo,
    )

    # ---- current or most recent session
    session = (
        await db.execute(
            select(MonitoringSession).where(MonitoringSession.patient_id == patient.id)
            .order_by(MonitoringSession.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    live = ingest.active_session(patient.id)
    if session is not None:
        ctx.session_id = session.id
        stats = live.summary() if live is not None and live.id == session.id else (session.stats or {})
        counts = stats.get("beat_counts") or {}
        state = "in progress" if live is not None and live.id == session.id else "ended"
        source = {"replay": "replay of a public labelled recording", "seed": "generated demo history",
                  "serial": "sensor kit"}.get(session.source, session.source)
        fact = (
            f"Most recent monitoring session ({state}, {source}) started {_when(session.started_at)}: "
            f"{(stats.get('duration_s') or 0) / 60:.1f} min, {stats.get('beats', 0)} beats "
            f"(N {counts.get('N', 0)}, S {counts.get('S', 0)}, V {counts.get('V', 0)}, F {counts.get('F', 0)}, "
            f"Unknown {counts.get('Unknown', 0)}), mean HR {stats.get('mean_hr_bpm') or 'n/a'} bpm, "
            f"V burden {stats.get('v_burden_pct', 0)}%, S burden {stats.get('s_burden_pct', 0)}%, "
            f"highest risk {stats.get('max_risk_score', 0)}/100 ({stats.get('max_risk_tier', 'green')}), "
            f"{stats.get('gaps', 0)} signal gaps. Model version {session.model_version or 'none (rules-only)'}, "
            f"rule version {session.rule_version}."
        )
        ctx.add(Evidence(f"S-{short(session.id)}", "session", f"Session {_when(session.started_at)}",
                         iso(session.started_at), fact))

    # ---- recent period vs 14-day baseline
    recent_start, recent_end = now - timedelta(hours=24), now + timedelta(minutes=1)
    buckets, _ = await trend_buckets(db, patient.id, recent_start, recent_end, 3600)
    if not buckets and session is not None:
        recent_start = session.started_at - timedelta(minutes=1)
        recent_end = (session.ended_at or now) + timedelta(minutes=1)
        buckets, _ = await trend_buckets(db, patient.id, recent_start, recent_end, 3600)
        ctx.recent_label = f"the most recent session ({_when(session.started_at)})"
    ctx.recent = summarise(buckets)
    base_buckets, _ = await trend_buckets(
        db, patient.id, recent_start - timedelta(days=BASELINE_DAYS), recent_start, 6 * 3600
    )
    ctx.baseline = summarise(base_buckets)
    recent, base = ctx.recent, ctx.baseline
    if recent["n_windows"]:
        ctx.add(Evidence("T-recent", "trend", f"Summary of {ctx.recent_label}", iso(recent_start),
                         _period_fact(f"In {ctx.recent_label}", recent)))
    if base["n_windows"]:
        ctx.add(Evidence("T-baseline", "trend", f"{BASELINE_DAYS}-day baseline",
                         iso(recent_start - timedelta(days=BASELINE_DAYS)),
                         _period_fact(f"Baseline, the {BASELINE_DAYS} days before that", base)))
    if recent["n_windows"] and base["n_windows"]:
        for key, tid, name, unit, digits in (
            ("hr_bpm", "T-hr", "Mean heart rate", " bpm", 0),
            ("v_burden_pct", "T-vburden", "Mean V burden", "%", 1),
            ("s_burden_pct", "T-sburden", "Mean S burden", "%", 1),
            ("rmssd_ms", "T-hrv", "HRV (RMSSD)", " ms", 0),
            ("risk_score_mean", "T-risk", "Mean risk score", "/100", 0),
        ):
            a, b = recent.get(key), base.get(key)
            if a is None or b is None:
                continue
            delta = a - b
            ctx.add(Evidence(
                tid, "trend", f"{name} trend", iso(recent_start),
                f"{name}: {a:.{digits}f}{unit} in {ctx.recent_label} versus {b:.{digits}f}{unit} over the "
                f"{BASELINE_DAYS}-day baseline (change {delta:+.{digits}f}{unit}).",
            ))

    # ---- rhythm windows: the latest one and the highest-risk ones of the recent period
    in_recent = (RhythmWindow.patient_id == patient.id, RhythmWindow.t >= recent_start, RhythmWindow.t < recent_end)
    latest = (
        await db.execute(select(RhythmWindow).where(*in_recent).order_by(RhythmWindow.t.desc()).limit(1))
    ).scalar_one_or_none()
    top = (
        await db.execute(
            select(RhythmWindow).where(*in_recent, RhythmWindow.risk_score >= 25)
            .order_by(RhythmWindow.risk_score.desc(), RhythmWindow.t.desc()).limit(3)
        )
    ).scalars().all()
    ctx.latest_window = latest
    seen: set[str] = set()
    for role, w in ([("Latest", latest)] if latest else []) + [("High-risk", w) for w in top]:
        if w.id in seen:
            continue
        seen.add(w.id)
        labels = ", ".join(f.get("label", f.get("rule_id", "")) for f in (w.findings or [])) or "no rule findings"
        ctx.add(Evidence(
            f"W-{short(w.id)}", "window", f"{role} rhythm window {_when(w.t)}", iso(w.t),
            f"{role} 10 s rhythm window ({_when(w.t)}): HR {w.hr_bpm if w.hr_bpm is not None else 'n/a'} bpm, "
            f"V burden {w.v_burden_pct or 0:.1f}%, S burden {w.s_burden_pct or 0:.1f}%, SQI {w.sqi or 0:.2f}, "
            f"risk {w.risk_score}/100 ({w.risk_tier}); findings: {labels}.",
        ))

    # ---- alerts: recent ones and anything still unresolved
    alerts = (
        await db.execute(
            select(Alert).where(
                Alert.patient_id == patient.id,
                or_(Alert.created_at >= recent_start, Alert.state.in_(("open", "escalated"))),
            ).order_by(Alert.created_at.desc()).limit(6)
        )
    ).scalars().all()
    ctx.alerts = list(alerts)
    for a in alerts:
        ack = f", acknowledged {_when(a.acknowledged_at)}" if a.acknowledged_at else ""
        ctx.add(Evidence(
            f"A-{short(a.id)}", "alert", f"{a.tier.capitalize()} alert: {a.title}", iso(a.created_at),
            f"{a.tier.capitalize()}-tier alert \"{a.title}\" (rule {a.rule_id}) raised {_when(a.created_at)}, "
            f"state {a.state}{ack}, seen {a.occurrences}×; detail: {a.detail or 'none'}.",
        ))

    # ---- notes and symptoms
    notes = (
        await db.execute(
            select(ClinicalNote).where(
                ClinicalNote.patient_id == patient.id, ClinicalNote.created_at >= now - timedelta(days=BASELINE_DAYS)
            ).order_by(ClinicalNote.created_at.desc()).limit(5)
        )
    ).scalars().all()
    ctx.notes = list(notes)
    for n in notes:
        kind = "Patient-reported symptom" if n.kind == "symptom" else "Clinician note"
        ctx.add(Evidence(f"N-{short(n.id)}", "note", f"{kind} {_when(n.created_at)}", iso(n.created_at),
                         f"{kind} ({_when(n.created_at)}): {scrub(n.text, patient)[:400]}"))

    # ---- signal quality
    if recent.get("sqi") is not None:
        ctx.add(Evidence(
            "Q-recent", "quality", "Signal quality", iso(recent_start),
            f"Mean signal quality index (SQI, 0-1) in {ctx.recent_label}: {recent['sqi']:.2f}; current signal status: "
            f"{patient.signal_status}. Low SQI suppresses clinical alerts.",
        ))
    return ctx
