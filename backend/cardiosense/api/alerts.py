"""Alert queue and clinician workflow (FR-15, FR-20). Alerts are never closed automatically."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_ingest
from cardiosense.db import get_db, utcnow
from cardiosense.models import Alert, AlertDelivery, Patient, User
from cardiosense.schemas import AlertActionIn
from cardiosense.security import CLINICAL_ROLES, STAFF_ROLES, client_ip, is_uuid, load_patient, require_roles
from cardiosense.services import audit
from cardiosense.services.ingest import IngestManager
from cardiosense.services.serialize import alert_to_dict, iso

router = APIRouter(tags=["alerts"])

VIEW_ROLES = ("patient", *STAFF_ROLES)

# action -> (states it may be applied from, resulting state)
TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "ack": (("open", "escalated"), "acknowledged"),
    "escalate": (("open", "acknowledged"), "escalated"),
    "close": (("open", "acknowledged", "escalated"), "closed"),
}

_TIER_ORDER = case({"red": 0, "orange": 1, "amber": 2, "green": 3}, value=Alert.tier, else_=4)


async def _load_alert(db: AsyncSession, user: User, alert_id: str) -> tuple[Alert, Patient]:
    alert = await db.get(Alert, alert_id) if is_uuid(alert_id) else None
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    try:
        patient = await load_patient(db, user, alert.patient_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found") from None
        raise
    return alert, patient


@router.get("/alerts")
async def list_alerts(
    state: Literal["open", "acknowledged", "escalated", "closed"] | None = None,
    tier: Literal["green", "amber", "orange", "red"] | None = None,
    patient_id: str | None = None,
    evidence: bool = Query(default=True, description="include the evidence snapshot of every alert"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_roles(*VIEW_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """The alert queue: red first, then newest first."""
    stmt = select(Alert, Patient.name).join(Patient, Patient.id == Alert.patient_id)
    if user.role == "patient":
        stmt = stmt.where(Alert.patient_id == user.patient_id)
    else:
        stmt = stmt.where(Alert.org_id == user.org_id)
    if patient_id:
        patient = await load_patient(db, user, patient_id)
        stmt = stmt.where(Alert.patient_id == patient.id)
    if state:
        stmt = stmt.where(Alert.state == state)
    if tier:
        stmt = stmt.where(Alert.tier == tier)
    rows = await db.execute(stmt.order_by(_TIER_ORDER, Alert.created_at.desc()).limit(limit))
    out = []
    for alert, name in rows:
        item = alert_to_dict(alert, with_evidence=evidence)
        item["patient_name"] = name
        out.append(item)
    return JSONResponse(out)


@router.get("/alerts/{alert_id}")
async def get_alert(
    alert_id: str,
    request: Request,
    user: User = Depends(require_roles(*VIEW_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    alert, patient = await _load_alert(db, user, alert_id)
    out = alert_to_dict(alert)
    out["patient_name"] = patient.name
    deliveries = await db.execute(
        select(AlertDelivery).where(AlertDelivery.alert_id == alert.id).order_by(AlertDelivery.created_at)
    )
    out["deliveries"] = [
        {"channel": d.channel, "status": d.status, "attempts": d.attempts, "last_error": d.last_error,
         "at": iso(d.updated_at)}
        for d in deliveries.scalars()
    ]
    audit.record_view(db, user, "alert", alert.id, ip=client_ip(request))
    await db.commit()
    return JSONResponse(out)


async def _transition(
    action: str, alert_id: str, body: AlertActionIn | None, request: Request, user: User, db: AsyncSession,
    ingest: IngestManager,
) -> JSONResponse:
    alert, patient = await _load_alert(db, user, alert_id)
    allowed_from, new_state = TRANSITIONS[action]
    if alert.state not in allowed_from:
        raise HTTPException(status.HTTP_409_CONFLICT, f"An alert that is {alert.state} cannot be {new_state}")
    now = utcnow()
    note = body.note.strip() if body and body.note and body.note.strip() else None
    previous = alert.state
    alert.state = new_state
    alert.updated_at = now
    if note:
        alert.note = note
    if action == "ack":
        alert.acknowledged_by, alert.acknowledged_by_id, alert.acknowledged_at = user.name, user.id, now
    entry = {"action": action, "state": new_state, "from_state": previous, "to_state": new_state, "by": user.name,
             "actor_email": user.email, "by_id": user.id, "at": iso(now), "note": note}
    alert.history = [*(alert.history or []), entry]
    audit.record(db, user, f"alert.{action}", "alert", alert.id,
                 {"from": previous, "to": new_state, "rule_id": alert.rule_id, "tier": alert.tier,
                  "patient_id": alert.patient_id, "has_note": note is not None}, ip=client_ip(request))
    await db.commit()
    out = alert_to_dict(alert)
    ingest.alert_changed(dict(out))
    out["patient_name"] = patient.name
    return JSONResponse(out)


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: str, request: Request, body: AlertActionIn | None = None,
    user: User = Depends(require_roles(*CLINICAL_ROLES)), db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> JSONResponse:
    return await _transition("ack", alert_id, body, request, user, db, ingest)


@router.post("/alerts/{alert_id}/escalate")
async def escalate_alert(
    alert_id: str, request: Request, body: AlertActionIn | None = None,
    user: User = Depends(require_roles(*CLINICAL_ROLES)), db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> JSONResponse:
    return await _transition("escalate", alert_id, body, request, user, db, ingest)


@router.post("/alerts/{alert_id}/close")
async def close_alert(
    alert_id: str, request: Request, body: AlertActionIn | None = None,
    user: User = Depends(require_roles(*CLINICAL_ROLES)), db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> JSONResponse:
    return await _transition("close", alert_id, body, request, user, db, ingest)
