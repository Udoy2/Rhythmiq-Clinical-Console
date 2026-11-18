"""Monitoring sessions: metadata, beats, events, stored signal (replay / episode strips) and CSV export."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import AsyncIterator
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_ingest, load_session
from cardiosense.config import DISCLAIMER
from cardiosense.db import get_db, session_factory
from cardiosense.models import Alert, Beat, ClinicalNote, MonitoringSession, RhythmWindow, SessionGap, SignalChunk, User
from cardiosense.security import client_ip, current_user, load_patient
from cardiosense.services import audit
from cardiosense.services.ingest import IngestManager
from cardiosense.services.serialize import alert_to_dict, beat_to_dict, iso, session_to_dict, window_to_dict

router = APIRouter(tags=["sessions"])

MAX_SIGNAL_S = 300.0
MAX_BEATS = 50_000


def _session_out(s: MonitoringSession, ingest: IngestManager) -> dict[str, Any]:
    live = ingest.session_by_id(s.id)
    out = session_to_dict(s, active=live is not None)
    if live is not None:  # the stored stats lag the stream by up to one window
        out["stats"] = live.summary()
        out["duration_s"] = out["stats"]["duration_s"]
    elif s.ended_at is None:
        out["active"] = False  # interrupted without a clean end (e.g. server restart)
    out["summary"] = out["stats"]
    return out


@router.get("/patients/{patient_id}/sessions")
async def list_sessions(
    patient_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> list[dict[str, Any]]:
    patient = await load_patient(db, user, patient_id)
    rows = await db.execute(
        select(MonitoringSession).where(MonitoringSession.patient_id == patient.id)
        .order_by(MonitoringSession.started_at.desc()).limit(limit)
    )
    return [_session_out(s, ingest) for s in rows.scalars()]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> dict[str, Any]:
    session, _ = await load_session(db, user, session_id)
    return _session_out(session, ingest)


@router.get("/sessions/{session_id}/beats")
async def session_beats(
    session_id: str,
    from_s: float | None = Query(default=None, alias="from", ge=0),
    to_s: float | None = Query(default=None, alias="to", ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Beat labels for a time range in seconds since session start (both bounds optional)."""
    session, _ = await load_session(db, user, session_id)
    stmt = select(Beat).where(Beat.session_id == session.id)
    if from_s is not None:
        stmt = stmt.where(Beat.sample >= math.floor(from_s * session.fs))
    if to_s is not None:
        stmt = stmt.where(Beat.sample <= math.ceil(to_s * session.fs))
    rows = await db.execute(stmt.order_by(Beat.sample).limit(MAX_BEATS))
    return [beat_to_dict(b) for b in rows.scalars()]


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    kinds: str | None = Query(default=None, description="comma list of window,alert,gap,note (default: all)"),
    findings_only: bool = Query(default=False, description="only rhythm windows that have findings"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Rhythm windows, alerts, signal gaps and episode annotations of a session, ordered by time."""
    session, _ = await load_session(db, user, session_id)
    wanted = {k.strip() for k in kinds.split(",")} if kinds else {"window", "alert", "gap", "note"}
    events: list[dict[str, Any]] = []
    if "window" in wanted:
        rows = await db.execute(
            select(RhythmWindow).where(RhythmWindow.session_id == session.id).order_by(RhythmWindow.start_s)
        )
        for w in rows.scalars():
            if findings_only and not w.findings:
                continue
            events.append({"kind": "window", **window_to_dict(w)})
    if "alert" in wanted:
        rows = await db.execute(select(Alert).where(Alert.session_id == session.id).order_by(Alert.created_at))
        for a in rows.scalars():
            evidence = a.evidence or {}
            events.append({
                "kind": "alert", "id": a.id, "alert_id": a.id, "rule_id": a.rule_id, "label": a.title, "title": a.title,
                "tier": a.tier, "severity": a.tier, "state": a.state, "detail": a.detail,
                "start_s": evidence.get("start_s"), "end_s": evidence.get("end_s"), "created_at": iso(a.created_at),
            })
    if "gap" in wanted:
        rows = await db.execute(select(SessionGap).where(SessionGap.session_id == session.id).order_by(SessionGap.at_sample))
        for g in rows.scalars():
            events.append({
                "kind": "gap", "id": f"gap-{g.id}", "label": "Signal gap", "start_s": round(g.at_sample / session.fs, 3),
                "end_s": round((g.at_sample + g.missing) / session.fs, 3), "missing_samples": g.missing,
                "detail": f"{g.missing / session.fs:.2f} s of signal missing (not interpolated)",
            })
    if "note" in wanted:
        rows = await db.execute(
            select(ClinicalNote).where(ClinicalNote.session_id == session.id).order_by(ClinicalNote.created_at)
        )
        for n in rows.scalars():
            events.append({"kind": "note", "id": n.id, "label": "Annotation" if n.kind == "note" else "Symptom",
                           "start_s": n.at_s, "end_s": None, "detail": n.text, "author_name": n.author_name,
                           "created_at": iso(n.created_at)})
    events.sort(key=lambda e: (e.get("start_s") if e.get("start_s") is not None else float("inf")))
    return events


@router.get("/sessions/{session_id}/signal")
async def session_signal(
    session_id: str,
    from_s: float = Query(default=0.0, alias="from", ge=0),
    to_s: float | None = Query(default=None, alias="to", ge=0),
    kind: Literal["display", "raw"] = "display",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Stored signal for ``[from, to)`` seconds (default 10 s, max 300 s per request).

    ``display`` is the filtered signal in mV exactly as it was shown live; ``raw`` is the device samples.
    Samples that were never received (gaps) are ``null`` — they are never interpolated.
    """
    session, _ = await load_session(db, user, session_id)
    if to_s is None:
        to_s = from_s + 10.0
    if to_s <= from_s:
        raise HTTPException(422, "'to' must be greater than 'from'")
    if to_s - from_s > MAX_SIGNAL_S:
        raise HTTPException(422, f"at most {MAX_SIGNAL_S:.0f} s per request")
    fs = session.fs
    first, last = int(round(from_s * fs)), int(round(to_s * fs))
    rows = await db.execute(
        select(SignalChunk)
        .where(SignalChunk.session_id == session.id, SignalChunk.kind == kind,
               SignalChunk.t0 < last, SignalChunk.t0 + SignalChunk.n > first)
        .order_by(SignalChunk.t0)
    )
    chunks = list(rows.scalars())
    if chunks:
        fs = chunks[0].fs
        last = min(last, max(c.t0 + c.n for c in chunks))
    else:
        last = first
    buf = np.full(max(last - first, 0), np.nan, dtype=np.float64)
    lead_off = False
    for c in chunks:
        data = np.frombuffer(c.data, dtype="<f4")
        lo, hi = max(c.t0, first), min(c.t0 + c.n, last)
        if hi > lo:
            buf[lo - first:hi - first] = data[lo - c.t0:hi - c.t0]
            lead_off = lead_off or c.lead_off
    missing = np.isnan(buf)
    values: list[float | None] = np.round(buf, 4).tolist()
    if missing.any():
        values = [None if m else v for v, m in zip(values, missing.tolist())]
    return {
        "session_id": session.id, "kind": kind, "fs": fs, "t0": first, "mv": values, "from_s": round(first / fs, 4),
        "to_s": round(last / fs, 4), "missing_samples": int(missing.sum()), "lead_off": lead_off,
        "units": "mV" if kind == "display" else "device units",
    }


@router.get("/sessions/{session_id}/export.csv")
async def export_session_csv(
    session_id: str,
    request: Request,
    table: Literal["beats", "windows", "alerts"] = "beats",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """CSV export. Header lines starting with ``#`` carry the disclaimer and the model / rule versions."""
    session, patient = await load_session(db, user, session_id)
    audit.record(db, user, "session.export_csv", "session", session.id, {"table": table}, ip=client_ip(request))
    await db.commit()
    header = [
        f"# CardioSense AI session export — {table}",
        f"# {DISCLAIMER}",
        f"# session_id={session.id} patient_id={session.patient_id} demo={'yes' if patient.is_demo else 'no'}",
        f"# source={session.source} record={session.record or ''} dataset={session.dataset or ''} fs={session.fs}",
        f"# started_at={iso(session.started_at)} ended_at={iso(session.ended_at) or ''}",
        f"# model_version={session.model_version or 'none (rules-only mode)'} rule_version={session.rule_version or ''}",
    ]
    sid = session.id

    async def body() -> AsyncIterator[str]:
        yield "\n".join(header) + "\n"
        out = io.StringIO()
        writer = csv.writer(out)

        def take() -> str:
            text = out.getvalue()
            out.seek(0)
            out.truncate()
            return text

        async with session_factory()() as s:
            if table == "beats":
                writer.writerow(["time_s", "sample", "label", "confidence", "p_N", "p_S", "p_V", "p_F", "truth",
                                 "model_version"])
                last = -1
                while True:
                    rows = (await s.execute(
                        select(Beat).where(Beat.session_id == sid, Beat.sample > last).order_by(Beat.sample).limit(5000)
                    )).scalars().all()
                    if not rows:
                        break
                    for b in rows:
                        p = b.probs or {}
                        writer.writerow([b.time_s, b.sample, b.label, b.confidence, p.get("N"), p.get("S"), p.get("V"),
                                         p.get("F"), b.truth or "", b.model_version or ""])
                    last = rows[-1].sample
                    yield take()
            elif table == "windows":
                writer.writerow(["start_s", "end_s", "t", "hr_bpm", "sdnn_ms", "rmssd_ms", "s_burden_pct",
                                 "v_burden_pct", "sqi", "risk_score", "risk_tier", "findings", "rule_version"])
                rows = (await s.execute(
                    select(RhythmWindow).where(RhythmWindow.session_id == sid).order_by(RhythmWindow.start_s)
                )).scalars().all()
                for w in rows:
                    writer.writerow([w.start_s, w.end_s, iso(w.t), w.hr_bpm, w.sdnn_ms, w.rmssd_ms, w.s_burden_pct,
                                     w.v_burden_pct, w.sqi, w.risk_score, w.risk_tier,
                                     ";".join(f.get("rule_id", "") for f in (w.findings or [])), w.rule_version or ""])
                yield take()
            else:
                writer.writerow(["id", "created_at", "rule_id", "tier", "title", "detail", "state", "acknowledged_by",
                                 "note", "start_s", "end_s", "rule_version", "model_version"])
                rows = (await s.execute(
                    select(Alert).where(Alert.session_id == sid).order_by(Alert.created_at)
                )).scalars().all()
                for a in rows:
                    d = alert_to_dict(a)
                    ev = d["evidence"]
                    writer.writerow([a.id, d["created_at"], a.rule_id, a.tier, a.title, a.detail or "", a.state,
                                     a.acknowledged_by or "", a.note or "", ev.get("start_s"), ev.get("end_s"),
                                     a.rule_version or "", a.model_version or ""])
                yield take()

    filename = f"cardiosense-session-{session.id[:8]}-{table}.csv"
    return StreamingResponse(body(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
