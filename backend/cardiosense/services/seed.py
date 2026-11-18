"""Idempotent demo seed (CONTRACT §4): organisation, users, three demo patients with paired devices, and
clearly flagged demo history (14 days of rhythm-window aggregates, a few past alerts, notes).

Everything created here is ``is_demo`` and never touches real patient records. Seeded sessions have
``source="seed"``: they hold aggregates only (no beats / waveform); alert evidence strips are cut from the
public MIT-BIH recordings in ``data/records`` together with their reference annotations.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.config import TIER_RANK, get_settings
from cardiosense.db import new_id, session_factory, utcnow
from cardiosense.models import (
    Alert,
    ClinicalNote,
    Device,
    MonitoringSession,
    Organisation,
    Patient,
    RhythmWindow,
    User,
)
from cardiosense.security import device_key_hash, hash_password
from cardiosense.services.demo import AAMI, MITBIH_FS
from cardiosense.services.serialize import iso

log = logging.getLogger("cardiosense.seed")

SEED_USERS = (
    ("admin@cardiosense.dev", "Demo Admin", "org_admin"),
    ("clinician@cardiosense.dev", "Dr. Nusrat (demo clinician)", "clinician"),
    ("tech@cardiosense.dev", "Farhana (demo technician)", "technician"),
    ("patient@cardiosense.dev", "Rahim (demo patient)", "patient"),
)

# record -> profile
DEMO_PATIENTS: dict[str, dict[str, Any]] = {
    "100": {"profile": "stable", "name": "Demo Patient — Stable (MIT-BIH 100)", "dob": date(1957, 4, 12), "sex": "M"},
    "223": {"profile": "watch", "name": "Demo Patient — Watch (MIT-BIH 223)", "dob": date(1953, 9, 3), "sex": "M"},
    "208": {"profile": "critical", "name": "Demo Patient — Critical (MIT-BIH 208)", "dob": date(1962, 1, 25), "sex": "F"},
}

RULE_LABELS = {
    "V_COUPLET": "V couplet", "V_RUN": "V run (non-sustained)", "BIGEMINY": "Bigeminy", "TRIGEMINY": "Trigeminy",
    "S_BURDEN": "S ectopic burden", "V_BURDEN": "V ectopic burden", "S_RUN": "S run",
}
RISK_POINTS = {"V_COUPLET": 30, "V_RUN": 60, "BIGEMINY": 35, "TRIGEMINY": 32, "S_BURDEN": 25, "V_BURDEN": 30, "S_RUN": 30}
TIER_FLOOR = {"green": 0, "amber": 25, "orange": 50, "red": 80}


# ====================================================================== entry point

async def run_seed(rule_version: str) -> None:
    settings = get_settings()
    async with session_factory()() as db:
        org = await _ensure_org(db)
        users = await _ensure_users(db, org)
        patients: dict[str, Patient] = {}
        for record, spec in DEMO_PATIENTS.items():
            patient, _ = await ensure_demo_patient(db, org.id, record, name=spec["name"], dob=spec["dob"],
                                                   sex=spec["sex"])
            patients[record] = patient
        demo_user = users["patient@cardiosense.dev"]
        if demo_user.patient_id is None:
            demo_user.patient_id = patients["100"].id
        await db.commit()
        if settings.seed_soak_devices > 0:
            await ensure_soak_devices(db, org.id, settings.seed_soak_devices)
            await db.commit()
        if settings.seed_history_days > 0:
            for record, patient in patients.items():
                await _seed_history(db, patient, record, DEMO_PATIENTS[record]["profile"], rule_version,
                                    users["clinician@cardiosense.dev"], users["patient@cardiosense.dev"])


async def _ensure_org(db: AsyncSession) -> Organisation:
    name = get_settings().default_org_name
    org = (await db.execute(select(Organisation).where(Organisation.name == name))).scalar_one_or_none()
    if org is None:
        org = Organisation(id=new_id(), name=name, plan="demo")
        db.add(org)
        await db.flush()
    return org


async def _ensure_users(db: AsyncSession, org: Organisation) -> dict[str, User]:
    out: dict[str, User] = {}
    password_hash: str | None = None
    for email, name, role in SEED_USERS:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            password_hash = password_hash or await hash_password(get_settings().seed_password)
            user = User(id=new_id(), org_id=org.id, email=email, name=name, role=role, password_hash=password_hash)
            db.add(user)
        out[email] = user
    await db.flush()
    return out


async def ensure_demo_patient(
    db: AsyncSession, org_id: str, record: str, *, name: str | None = None, dob: date | None = None,
    sex: str | None = None,
) -> tuple[Patient, Device]:
    """Find or create the demo patient and paired replay device for a recording (any record id)."""
    patient = (
        await db.execute(
            select(Patient).where(Patient.org_id == org_id, Patient.is_demo.is_(True), Patient.demo_record == record)
            .order_by(Patient.created_at).limit(1)
        )
    ).scalar_one_or_none()
    if patient is None:
        patient = Patient(
            id=new_id(), org_id=org_id, name=name or f"Demo Patient — MIT-BIH record {record}", mrn=f"DEMO-{record}",
            dob=dob, sex=sex, notes="Demo / replay patient: public MIT-BIH recording, not a real person.",
            consent=True, consent_at=utcnow(), is_demo=True, demo_record=record,
        )
        db.add(patient)
        await db.flush()
    device = await ensure_demo_device(db, org_id, record, patient.id)
    return patient, device


def demo_device_key(record: str) -> str:
    return f"demo-key-{record}"


async def ensure_demo_device(db: AsyncSession, org_id: str, record: str, patient_id: str) -> Device:
    """``dev-demo-<record>`` (suffixed for other organisations: device ids are global) paired to the patient."""
    device_id = f"dev-demo-{record}"
    device = await db.get(Device, device_id)
    if device is not None and device.org_id != org_id:
        device_id = f"dev-demo-{record}-{org_id[:8]}"
        device = await db.get(Device, device_id)
    if device is None:
        device = Device(id=device_id, org_id=org_id, name=f"Replay source — record {record}", is_demo=True,
                        key_hash=device_key_hash(demo_device_key(record)), paired_at=utcnow(), fs=MITBIH_FS)
        db.add(device)
    device.patient_id = patient_id
    await db.flush()
    return device


# ====================================================================== demo history

async def _seed_history(
    db: AsyncSession, patient: Patient, record: str, profile: str, rule_version: str, clinician: User,
    patient_user: User,
) -> None:
    already = await db.scalar(
        select(func.count()).select_from(MonitoringSession)
        .where(MonitoringSession.patient_id == patient.id, MonitoringSession.source == "seed")
    )
    if already:
        return
    days = get_settings().seed_history_days
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    sessions: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    last_window: dict[str, Any] | None = None
    for day in range(days, 0, -1):
        for hour, n_windows in ((9, 120), (20, 90)):
            start = (now - timedelta(days=day)).replace(hour=hour)
            if start >= now - timedelta(hours=1):
                continue
            rng = random.Random(f"{record}:{day}:{hour}")
            progress = 1 - day / max(days, 1)  # 0 (oldest) .. ~1 (most recent)
            session_id = new_id()
            session_windows = [
                _window(profile, rng, i, progress, session_id, patient.id, start, rule_version) for i in range(n_windows)
            ]
            windows.extend(session_windows)
            last_window = session_windows[-1]
            sessions.append(_session_row(session_id, patient.id, record, start, session_windows, rule_version))
    # Alerts first: they update the per-session alert counters before the session rows are written.
    alerts = await asyncio.to_thread(_alert_rows, patient, record, profile, sessions, rule_version, clinician)
    db.add_all(MonitoringSession(**s) for s in sessions)
    await db.flush()
    for i in range(0, len(windows), 1000):
        await db.execute(insert(RhythmWindow), windows[i:i + 1000])
    db.add_all(Alert(**a) for a in alerts)
    for note in _note_rows(patient, profile, clinician, patient_user, now):
        db.add(ClinicalNote(**note))

    if last_window is not None:
        patient.risk_score, patient.risk_tier = last_window["risk_score"], last_window["risk_tier"]
    if alerts:
        newest = max(alerts, key=lambda a: a["created_at"])
        patient.last_event = {"label": newest["title"], "tier": newest["tier"], "rule_id": newest["rule_id"],
                              "time": iso(newest["created_at"]), "alert_id": newest["id"]}
    await db.commit()
    log.info("seeded demo history for record %s: %d sessions, %d windows, %d alerts",
             record, len(sessions), len(windows), len(alerts))


def _finding(rule_id: str, severity: str, start_s: float, value: float, detail: str) -> dict[str, Any]:
    return {"rule_id": rule_id, "label": RULE_LABELS[rule_id], "severity": severity, "start_s": round(start_s, 1),
            "end_s": round(start_s + 10.0, 1), "value": round(value, 2), "detail": detail}


def _risk(findings: list[dict[str, Any]]) -> dict[str, Any]:
    if not findings:
        return {"score": 5, "tier": "green", "factors": []}
    ranked = sorted(findings, key=lambda f: -RISK_POINTS[f["rule_id"]])
    tier = max((f["severity"] for f in findings), key=TIER_RANK.__getitem__)
    raw = RISK_POINTS[ranked[0]["rule_id"]] + 0.3 * sum(RISK_POINTS[f["rule_id"]] for f in ranked[1:])
    score = int(min(99 if tier != "red" else 100, max(TIER_FLOOR[tier], round(raw))))
    if tier == "amber":
        score = min(score, TIER_FLOOR["orange"] - 1)
    factors = [{"text": f["detail"], "points": RISK_POINTS[f["rule_id"]] if i == 0 else round(0.3 * RISK_POINTS[f["rule_id"]]),
                "rule_id": f["rule_id"]} for i, f in enumerate(ranked)]
    return {"score": score, "tier": tier, "factors": factors}


def _window(profile: str, rng: random.Random, i: int, progress: float, session_id: str, patient_id: str,
            start: datetime, rule_version: str) -> dict[str, Any]:
    start_s = i * 10.0
    wave = math.sin(i / 19.0)
    findings: list[dict[str, Any]] = []
    if profile == "stable":
        hr = 66 + 4 * wave + rng.gauss(0, 1.5)
        sdnn, rmssd = 46 + rng.gauss(0, 4), 31 + rng.gauss(0, 3)
        s_burden = 0.0 if rng.random() < 0.85 else round(rng.uniform(0.2, 0.9), 2)
        v_burden = 0.0
        sqi = min(0.99, 0.95 + rng.gauss(0, 0.02))
    elif profile == "watch":
        hr = 82 + 5 * wave + rng.gauss(0, 2.5)
        sdnn, rmssd = 58 + rng.gauss(0, 6), 44 + rng.gauss(0, 5)
        s_burden = max(0.0, round(1.2 + 1.5 * progress + rng.gauss(0, 0.6), 2))
        v_burden = max(0.0, round(2.5 + 3.0 * progress + rng.gauss(0, 1.0), 2))
        sqi = min(0.98, 0.9 + rng.gauss(0, 0.04))
        if v_burden >= 1.0:
            findings.append(_finding("V_BURDEN", "amber", start_s, v_burden, f"V burden {v_burden:.1f}% over the last hour"))
        if s_burden >= 1.0:
            findings.append(_finding("S_BURDEN", "amber", start_s, s_burden, f"S burden {s_burden:.1f}% over the last hour"))
        if rng.random() < 0.06 + 0.06 * progress:
            findings.append(_finding("V_COUPLET", "amber", start_s, 2, "2 consecutive V beats"))
    else:  # critical
        hr = 97 + 8 * wave + rng.gauss(0, 4)
        sdnn, rmssd = 88 + rng.gauss(0, 10), 96 + rng.gauss(0, 12)
        s_burden = max(0.0, round(0.3 + rng.gauss(0, 0.2), 2))
        v_burden = max(0.0, round(13 + 12 * progress + rng.gauss(0, 3.0), 2))
        sqi = min(0.97, 0.86 + rng.gauss(0, 0.05))
        severity = "orange" if v_burden > 10 else "amber"
        findings.append(_finding("V_BURDEN", severity, start_s, v_burden, f"V burden {v_burden:.1f}% over the last hour"))
        roll = rng.random()
        if roll < 0.07 + 0.05 * progress:
            n = rng.choice((3, 3, 4, 5))
            findings.append(_finding("V_RUN", "orange", start_s, n, f"{n} consecutive V beats"))
        elif roll < 0.25:
            findings.append(_finding("V_COUPLET", "amber", start_s, 2, "2 consecutive V beats"))
        elif roll < 0.33:
            findings.append(_finding("BIGEMINY", "amber", start_s, 6, "V every 2nd beat for 6 cycles"))
    total = max(4, round(hr / 6))
    v = round(total * v_burden / 100)
    s = round(total * s_burden / 100)
    risk = _risk(findings)
    return {
        "id": new_id(), "session_id": session_id, "patient_id": patient_id, "t": start + timedelta(seconds=start_s + 10),
        "start_s": start_s, "end_s": start_s + 10.0, "hr_bpm": round(hr, 1), "sdnn_ms": round(max(sdnn, 5), 1),
        "rmssd_ms": round(max(rmssd, 5), 1), "s_burden_pct": s_burden, "v_burden_pct": v_burden,
        "sqi": round(max(sqi, 0.5), 3), "beat_counts": {"N": max(total - v - s, 0), "S": s, "V": v, "F": 0, "Unknown": 0},
        "findings": findings, "risk": risk, "risk_score": risk["score"], "risk_tier": risk["tier"],
        "rule_version": rule_version,
    }


def _session_row(session_id: str, patient_id: str, record: str, start: datetime, windows: list[dict[str, Any]],
                 rule_version: str) -> dict[str, Any]:
    counts = {k: sum(w["beat_counts"][k] for w in windows) for k in ("N", "S", "V", "F", "Unknown")}
    beats = sum(counts.values())
    duration = windows[-1]["end_s"]
    top = max(windows, key=lambda w: w["risk_score"])
    return {
        "id": session_id, "patient_id": patient_id, "device_id": f"dev-demo-{record}", "source": "seed",
        "record": record, "dataset": "MIT-BIH", "fs": MITBIH_FS, "model_version": None, "rule_version": rule_version,
        "model_loaded": False, "is_demo": True, "started_at": start, "ended_at": start + timedelta(seconds=duration),
        "last_sample": int(duration * MITBIH_FS),
        "stats": {
            "seeded_history": True,
            "note": "Generated demo history: rhythm-window aggregates only, no stored beats or waveform.",
            "duration_s": duration, "beats": beats, "beat_counts": counts,
            "s_burden_pct": round(100 * counts["S"] / beats, 2) if beats else 0.0,
            "v_burden_pct": round(100 * counts["V"] / beats, 2) if beats else 0.0,
            "windows": len(windows), "mean_hr_bpm": round(sum(w["hr_bpm"] for w in windows) / len(windows), 1),
            "mean_sqi": round(sum(w["sqi"] for w in windows) / len(windows), 3),
            "max_risk_score": top["risk_score"], "max_risk_tier": top["risk_tier"], "alerts": 0, "gaps": 0,
            "missing_samples": 0,
        },
    }


# ---------------------------------------------------------------------- seeded alerts

def _load_record_head(record: str, seconds: int = 180) -> tuple[list[float], list[tuple[int, str]]]:
    """First ``seconds`` of a record in mV plus its reference beat annotations (sample, AAMI class)."""
    data_dir: Path = get_settings().data_dir
    csv_path, ann_path = data_dir / f"{record}.csv", data_dir / f"{record}annotations.txt"
    limit = seconds * MITBIH_FS
    mv: list[float] = []
    try:
        with csv_path.open("r", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split(",")
                if len(parts) < 2:
                    continue
                mv.append((float(parts[1]) - 1024.0) / 200.0)
                if len(mv) >= limit:
                    break
    except (OSError, ValueError):
        return [], []
    beats: list[tuple[int, str]] = []
    try:
        with ann_path.open("r", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) < 3 or not parts[1].isdigit():
                    continue
                sample = int(parts[1])
                if sample >= len(mv):
                    break
                cls = AAMI.get(parts[2])
                if cls:
                    beats.append((sample, cls))
    except OSError:
        pass
    return mv, beats


def _find_episode(beats: list[tuple[int, str]], rule_id: str) -> int | None:
    """Index into ``beats`` where the pattern of ``rule_id`` first appears in the reference annotations."""
    labels = "".join(c for _, c in beats)
    patterns = {"V_RUN": "VVV", "V_COUPLET": "VV", "BIGEMINY": "NVNVNV", "TRIGEMINY": "NNVNNV", "S_RUN": "SSS"}
    if rule_id in patterns:
        idx = labels.find(patterns[rule_id])
        return idx if idx >= 0 else None
    target = "S" if rule_id == "S_BURDEN" else "V"
    idx = labels.find(target)
    return idx if idx >= 0 else None


def _evidence(mv: list[float], beats: list[tuple[int, str]], rule_id: str, record: str, rule_version: str,
              window: dict[str, Any] | None) -> dict[str, Any]:
    idx = _find_episode(beats, rule_id) if beats else None
    centre = beats[idx][0] if idx is not None else 5 * MITBIH_FS
    start = max(0, min(centre - 3 * MITBIH_FS, max(len(mv) - 10 * MITBIH_FS, 0)))
    end = min(len(mv), start + 10 * MITBIH_FS)
    strip = mv[start:end]
    baseline = sorted(strip)[len(strip) // 2] if strip else 0.0
    return {
        "start_s": round(start / MITBIH_FS, 3), "end_s": round(end / MITBIH_FS, 3), "fs": MITBIH_FS,
        "strip_mv": [round(x - baseline, 4) for x in strip],
        "beats": [
            {"sample": s, "time_s": round(s / MITBIH_FS, 3), "label": c, "confidence": None, "probs": None,
             "model_version": None, "truth": c}
            for s, c in beats if start <= s < end
        ],
        "window": {k: window[k] for k in ("start_s", "end_s", "hr_bpm", "s_burden_pct", "v_burden_pct", "sqi",
                                          "findings", "risk")} if window else None,
        "rule_version": rule_version, "model_version": None,
        "seeded": True,
        "note": f"Demo history: strip and beat labels are the reference signal and annotations of MIT-BIH record "
                f"{record}, not model output.",
    }


def _alert_rows(patient: Patient, record: str, profile: str, sessions: list[dict[str, Any]], rule_version: str,
                clinician: User) -> list[dict[str, Any]]:
    """(rule, tier, title, detail, state, days ago, note) per profile. Runs in a worker thread (file I/O)."""
    plans: dict[str, list[tuple[str, str, str, str, str, int, str | None]]] = {
        "stable": [],
        "watch": [
            ("V_BURDEN", "amber", "V ectopic burden", "V burden 4.2% over the last hour", "closed", 10,
             "Reviewed; isolated ectopy, continue monitoring."),
            ("S_BURDEN", "amber", "S ectopic burden", "S burden 2.1% over the last hour", "acknowledged", 4, None),
            ("V_COUPLET", "amber", "V couplet", "2 consecutive V beats", "open", 1, None),
        ],
        "critical": [
            ("BIGEMINY", "amber", "Bigeminy", "V every 2nd beat for 6 cycles", "closed", 9,
             "Seen with clinician; documented."),
            ("V_BURDEN", "orange", "V ectopic burden", "V burden 18.4% over the last hour", "acknowledged", 5,
             "Burden rising versus last week; review scheduled."),
            ("V_RUN", "orange", "V run (non-sustained)", "4 consecutive V beats", "escalated", 2,
             "Escalated for senior review."),
            ("V_RUN", "orange", "V run (non-sustained)", "3 consecutive V beats", "open", 1, None),
        ],
    }
    plan = plans.get(profile, [])
    if not plan or not sessions:
        return []
    mv, beats = _load_record_head(record)
    rows = []
    by_start = sorted(sessions, key=lambda s: s["started_at"])
    for rule_id, tier, title, detail, state, days_ago, note in plan:
        target = datetime.now(timezone.utc) - timedelta(days=days_ago)
        session = min(by_start, key=lambda s: abs((s["started_at"] - target).total_seconds()))
        created = session["started_at"] + timedelta(minutes=7, seconds=13 * len(rows))
        history: list[dict[str, Any]] = []
        acked_at = None
        if state != "open":
            acked_at = created + timedelta(minutes=11)
            history.append({"action": "ack", "state": "acknowledged", "from_state": "open", "to_state": "acknowledged",
                            "by": clinician.name, "actor_email": clinician.email, "by_id": clinician.id,
                            "at": iso(acked_at), "note": note if state == "acknowledged" else None})
            if state in ("escalated", "closed"):
                action = "escalate" if state == "escalated" else "close"
                history.append({"action": action, "state": state, "from_state": "acknowledged", "to_state": state,
                                "by": clinician.name, "actor_email": clinician.email, "by_id": clinician.id,
                                "at": iso(acked_at + timedelta(minutes=20)), "note": note})
        rows.append({
            "id": new_id(), "org_id": patient.org_id, "patient_id": patient.id, "session_id": session["id"],
            "rule_id": rule_id, "tier": tier, "title": title, "detail": detail, "state": state, "is_demo": True,
            "created_at": created, "updated_at": (acked_at + timedelta(minutes=20)) if acked_at else created,
            "occurrences": 1, "last_seen_at": created,
            "acknowledged_by": clinician.name if acked_at else None,
            "acknowledged_by_id": clinician.id if acked_at else None, "acknowledged_at": acked_at,
            "note": note, "history": history,
            "evidence": _evidence(mv, beats, rule_id, record, rule_version, None) if mv else
            {"seeded": True, "rule_version": rule_version, "model_version": None, "strip_mv": [], "beats": [],
             "fs": MITBIH_FS, "start_s": 0.0, "end_s": 0.0},
            "rule_version": rule_version, "model_version": None,
        })
        session["stats"]["alerts"] += 1
    return rows


def _note_rows(patient: Patient, profile: str, clinician: User, patient_user: User, now: datetime) -> list[dict[str, Any]]:
    plans = {
        "stable": [(6, "note", clinician, "Baseline recording reviewed; rhythm regular, no ectopy of note. (demo note)")],
        "watch": [
            (8, "symptom", patient_user, "Felt skipped beats for a few minutes after climbing stairs. (demo note)"),
            (4, "note", clinician, "Ectopic burden slightly higher than last week; continue monitoring. (demo note)"),
        ],
        "critical": [
            (6, "symptom", patient_user, "Palpitations and light-headedness in the evening, about ten minutes. (demo note)"),
            (5, "note", clinician, "V burden rising over the past week; follow-up review booked. (demo note)"),
            (2, "note", clinician, "Short V runs seen on the morning session; escalated for senior review. (demo note)"),
        ],
    }
    return [
        {"id": new_id(), "patient_id": patient.id, "author_id": author.id if kind == "note" else None,
         "author_name": author.name if kind == "note" else "Patient-reported (demo)", "kind": kind,
         "text": text, "created_at": now - timedelta(days=days, hours=2)}
        for days, kind, author, text in plans.get(profile, [])
    ]


# ====================================================================== soak devices

async def ensure_soak_devices(db: AsyncSession, org_id: str, count: int) -> list[dict[str, str]]:
    """Demo patients with paired devices ``dev-soak-NNN`` / keys ``soak-key-NNN`` (NNN = 000..count-1) for
    ``sender/multi_sender.py``. Idempotent; one demo patient per stream so sessions do not collide."""
    out = []
    for i in range(count):
        tag = f"{i:03d}"
        record = f"soak-{tag}"
        patient = (
            await db.execute(
                select(Patient).where(Patient.org_id == org_id, Patient.is_demo.is_(True), Patient.demo_record == record)
                .limit(1)
            )
        ).scalar_one_or_none()
        if patient is None:
            patient = Patient(id=new_id(), org_id=org_id, name=f"Soak stream {tag} (demo)", mrn=f"SOAK-{tag}",
                              notes="Load-test demo patient; replayed public recordings only.", consent=True,
                              consent_at=utcnow(), is_demo=True, demo_record=record)
            db.add(patient)
            await db.flush()
        device_id = f"dev-soak-{tag}"
        device = await db.get(Device, device_id)
        if device is None:
            device = Device(id=device_id, org_id=org_id, name=f"Soak replay source {tag}", is_demo=True,
                            key_hash=device_key_hash(f"soak-key-{tag}"), paired_at=utcnow(), fs=MITBIH_FS)
            db.add(device)
        if device.org_id == org_id:
            device.patient_id = patient.id
            out.append({"device_id": device_id, "device_key": f"soak-key-{tag}", "patient_id": patient.id})
    await db.flush()
    return out


async def remove_soak_devices(db: AsyncSession, org_id: str) -> int:
    """Delete the soak demo patients of an organisation with their devices, sessions and alerts."""
    patients = (
        await db.execute(
            select(Patient).where(Patient.org_id == org_id, Patient.is_demo.is_(True), Patient.demo_record.like("soak-%"))
        )
    ).scalars().all()
    for patient in patients:
        for device in (await db.execute(select(Device).where(Device.patient_id == patient.id))).scalars():
            await db.delete(device)
        await db.delete(patient)
    await db.flush()
    return len(patients)
