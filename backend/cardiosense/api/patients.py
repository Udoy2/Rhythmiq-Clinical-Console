"""Patients, consent, clinical notes and trends (FR-05, FR-17)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_ingest
from cardiosense.config import TIER_RANK
from cardiosense.db import get_db, new_id, utcnow
from cardiosense.models import Alert, ClinicalNote, Device, Patient, User
from cardiosense.schemas import NoteCreate, NoteOut, PatientCreate, PatientOut, PatientUpdate
from cardiosense.security import (
    CLINICAL_ROLES,
    STAFF_ROLES,
    client_ip,
    current_user,
    is_uuid,
    load_patient,
    require_roles,
)
from cardiosense.services import audit
from cardiosense.services.ingest import IngestManager
from cardiosense.services.serialize import iso
from cardiosense.services.trends import RANGES, summarise, trend_buckets, window_bounds

router = APIRouter(tags=["patients"])

UNRESOLVED_STATES = ("open", "escalated")


async def patients_out(db: AsyncSession, ingest: IngestManager, patients: list[Patient]) -> list[PatientOut]:
    """Attach computed fields: paired device, unresolved alert count, active session."""
    ids = [p.id for p in patients]
    devices: dict[str, str] = {}
    alerts: dict[str, int] = {}
    if ids:
        for pid, did in await db.execute(
            select(Device.patient_id, Device.id).where(Device.patient_id.in_(ids)).order_by(Device.created_at.desc())
        ):
            devices[pid] = did  # oldest pairing wins
        for pid, n in await db.execute(
            select(Alert.patient_id, func.count()).where(Alert.patient_id.in_(ids), Alert.state.in_(UNRESOLVED_STATES))
            .group_by(Alert.patient_id)
        ):
            alerts[pid] = n
    out = []
    for p in patients:
        live = ingest.active_session(p.id)
        out.append(PatientOut(
            id=p.id, name=p.name, mrn=p.mrn, dob=p.dob, sex=p.sex, notes=p.notes, consent=p.consent,
            consent_at=p.consent_at, is_demo=p.is_demo, demo_record=p.demo_record,
            risk_score=p.risk_score, risk_tier=p.risk_tier, last_event=p.last_event,  # type: ignore[arg-type]
            signal_status=p.signal_status if live or p.signal_status == "offline" else "offline",
            active_session_id=live.id if live else None,
            device_id=live.device_id if live else devices.get(p.id),
            open_alerts=alerts.get(p.id, 0), created_at=p.created_at,
        ))
    return out


@router.get("/patients", response_model=list[PatientOut])
async def list_patients(
    q: str | None = Query(default=None, max_length=100),
    risk: Literal["green", "amber", "orange", "red"] | None = None,
    demo: bool | None = None,
    user: User = Depends(require_roles("patient", *STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> list[PatientOut]:
    """Patients the caller may see, highest risk first. A patient account sees only its own record."""
    stmt = select(Patient)
    if user.role == "patient":
        stmt = stmt.where(Patient.id == user.patient_id)
    else:
        stmt = stmt.where(Patient.org_id == user.org_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Patient.name.ilike(like), Patient.mrn.ilike(like)))
    if risk:
        stmt = stmt.where(Patient.risk_tier == risk)
    if demo is not None:
        stmt = stmt.where(Patient.is_demo.is_(demo))
    patients = list((await db.execute(stmt)).scalars())
    patients.sort(key=lambda p: (-TIER_RANK.get(p.risk_tier, 0), -p.risk_score, p.name.lower()))
    return await patients_out(db, ingest, patients)


@router.post("/patients", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient(
    body: PatientCreate,
    request: Request,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> PatientOut:
    patient = Patient(
        id=new_id(), org_id=user.org_id, name=body.name, mrn=body.mrn, dob=body.dob, sex=body.sex, notes=body.notes,
        consent=body.consent, consent_at=utcnow() if body.consent else None,
    )
    db.add(patient)
    audit.record(db, user, "patient.create", "patient", patient.id, {"consent": body.consent}, ip=client_ip(request))
    await db.commit()
    return (await patients_out(db, ingest, [patient]))[0]


@router.get("/patients/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> PatientOut:
    patient = await load_patient(db, user, patient_id)
    out = (await patients_out(db, ingest, [patient]))[0]
    audit.record_view(db, user, "patient", patient.id, ip=client_ip(request))
    await db.commit()
    return out


@router.patch("/patients/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str,
    body: PatientUpdate,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> PatientOut:
    """Edit a profile. A patient account may only give or withdraw its own consent."""
    patient = await load_patient(db, user, patient_id)
    changes = body.model_dump(exclude_unset=True)
    if user.role == "patient" and set(changes) - {"consent"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Patients can only change their consent")
    if changes.get("name") is None:
        changes.pop("name", None)
    if changes.get("consent") is None:
        changes.pop("consent", None)
    changed = {k: v for k, v in changes.items() if getattr(patient, k) != v}
    withdrew = False
    for key, value in changed.items():
        setattr(patient, key, value)
    if "consent" in changed:
        patient.consent_at = utcnow() if patient.consent else None
        withdrew = not patient.consent
    if changed:
        # Field names only: the audit trail must not duplicate clinical content.
        audit.record(db, user, "patient.update", "patient", patient.id,
                     {"fields": sorted(changed), **({"consent": patient.consent} if "consent" in changed else {})},
                     ip=client_ip(request))
    await db.commit()
    if withdrew:
        await ingest.stop_patient(patient.id, "patient consent withdrawn")
    return (await patients_out(db, ingest, [patient]))[0]


# ------------------------------------------------------------------ notes

@router.get("/patients/{patient_id}/notes", response_model=list[NoteOut])
async def list_notes(
    patient_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[ClinicalNote]:
    patient = await load_patient(db, user, patient_id)
    rows = await db.execute(
        select(ClinicalNote).where(ClinicalNote.patient_id == patient.id).order_by(ClinicalNote.created_at.desc())
    )
    return list(rows.scalars())


@router.post("/patients/{patient_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def add_note(
    patient_id: str,
    body: NoteCreate,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ClinicalNote:
    """Clinicians and org admins annotate; a patient may report symptoms on their own record."""
    patient = await load_patient(db, user, patient_id)
    if user.role == "patient":
        kind = "symptom"
    elif user.role in CLINICAL_ROLES:
        kind = body.kind
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot add clinical notes")
    if body.session_id is not None and not is_uuid(body.session_id):
        raise HTTPException(422, "session_id must be a session id")
    note = ClinicalNote(
        id=new_id(), patient_id=patient.id, author_id=user.id, author_name=user.name, kind=kind, text=body.text,
        session_id=body.session_id, at_s=body.at_s, created_at=utcnow(),
    )
    db.add(note)
    audit.record(db, user, "note.create", "patient", patient.id, {"note_id": note.id, "kind": kind},
                 ip=client_ip(request))
    await db.commit()
    return note


# ------------------------------------------------------------------ trends

@router.get("/patients/{patient_id}/trends")
async def patient_trends(
    patient_id: str,
    range: Literal["24h", "7d", "14d", "30d", "90d"] = Query(default="7d"),  # noqa: A002 - contract name
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """HR, HRV, S/V burden, risk and findings in time buckets; empty stretches are returned as gaps."""
    patient = await load_patient(db, user, patient_id)
    assert range in RANGES
    start, end, bucket_s = window_bounds(range)
    buckets, gaps = await trend_buckets(db, patient.id, start, end, bucket_s)
    return {
        "patient_id": patient.id,
        "range": range,
        "bucket_s": bucket_s,
        "from": iso(start),
        "to": iso(end),
        "buckets": buckets,
        "gaps": gaps,
        "summary": summarise(buckets),
        "is_demo": patient.is_demo,
    }
