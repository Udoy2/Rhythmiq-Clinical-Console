"""Demo mode (FR-22): replay public labelled recordings through the real ingest path."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.db import get_db
from cardiosense.models import Patient, User
from cardiosense.schemas import DemoReplayIn, DemoStopIn, SoakDevicesIn
from cardiosense.security import STAFF_ROLES, client_ip, load_patient, require_roles
from cardiosense.services import audit
from cardiosense.services.demo import DemoManager
from cardiosense.services.seed import (
    demo_device_key,
    ensure_demo_device,
    ensure_demo_patient,
    ensure_soak_devices,
    remove_soak_devices,
)

router = APIRouter(tags=["demo"])


def _demo(request: Request) -> DemoManager:
    return request.app.state.demo


@router.get("/demo/records")
async def demo_records(
    request: Request, user: User = Depends(require_roles(*STAFF_ROLES)), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    """Recordings available for replay (``data/records/<id>.csv`` + ``<id>annotations.txt``)."""
    demo = _demo(request)
    records = await demo.list_records()
    patients = {
        rec: pid
        for rec, pid in await db.execute(
            select(Patient.demo_record, Patient.id)
            .where(Patient.org_id == user.org_id, Patient.is_demo.is_(True), Patient.demo_record.is_not(None))
            .order_by(Patient.created_at.desc())
        )
    }
    running = {r["record"]: r for r in demo.status(user.org_id) if r["running"]}
    for info in records:
        info["patient_id"] = patients.get(info["record"])
        info["device_id"] = f"dev-demo-{info['record']}"
        info["running"] = info["record"] in running
    return records


@router.get("/demo/status")
async def demo_status(request: Request, user: User = Depends(require_roles(*STAFF_ROLES))) -> list[dict[str, Any]]:
    return _demo(request).status(user.org_id)


@router.post("/demo/replay")
async def demo_replay(
    body: DemoReplayIn,
    request: Request,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Start streaming a recording into a demo patient. Demo data never goes to a real patient record."""
    demo = _demo(request)
    if demo.record_path(body.record) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Recording {body.record} is not available")
    if body.patient_id:
        patient = await load_patient(db, user, body.patient_id)
        if not patient.is_demo:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Replays can only be streamed to demo patients, never to a real patient record")
        device = await ensure_demo_device(db, user.org_id, body.record, patient.id)
    else:
        patient, device = await ensure_demo_patient(db, user.org_id, body.record)
    if not patient.consent:
        raise HTTPException(status.HTTP_409_CONFLICT, "Consent has been withdrawn for this demo patient")
    audit.record(db, user, "demo.replay_start", "patient", patient.id,
                 {"record": body.record, "dataset": body.dataset, "speed": body.speed}, ip=client_ip(request))
    await db.commit()
    try:
        replay = await demo.start(
            record=body.record, dataset=body.dataset, speed=body.speed, patient_id=patient.id, device_id=device.id,
            device_key=demo_device_key(body.record), org_id=user.org_id, start_s=body.start_s,
            duration_s=body.duration_s, loop=body.loop,
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return {"status": "started", "is_demo": True, **replay.to_dict()}


@router.post("/demo/stop")
async def demo_stop(
    request: Request,
    body: DemoStopIn | None = None,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Stop the replay of a record / patient, or every replay of the organisation when none is given."""
    stopped = await _demo(request).stop(
        org_id=user.org_id, record=body.record if body else None, patient_id=body.patient_id if body else None
    )
    audit.record(db, user, "demo.replay_stop", None, None, {"stopped": [s["record"] for s in stopped]},
                 ip=client_ip(request))
    await db.commit()
    return {"status": "stopped", "stopped": stopped}


@router.post("/demo/soak-devices")
async def create_soak_devices(
    body: SoakDevicesIn,
    request: Request,
    user: User = Depends(require_roles("technician", "org_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create (idempotently) ``count`` paired demo devices ``dev-soak-NNN`` with keys ``soak-key-NNN``, each on
    its own demo patient, for ``sender/multi_sender.py`` load tests."""
    devices = await ensure_soak_devices(db, user.org_id, body.count)
    audit.record(db, user, "demo.soak_devices_create", None, None, {"count": len(devices)}, ip=client_ip(request))
    await db.commit()
    return {"count": len(devices), "devices": devices, "is_demo": True}


@router.delete("/demo/soak-devices")
async def delete_soak_devices(
    request: Request,
    user: User = Depends(require_roles("technician", "org_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Remove the soak demo patients, their devices and everything they recorded."""
    for replay in _demo(request).status(user.org_id):
        if replay["device_id"].startswith("dev-soak-"):
            await _demo(request).stop(device_id=replay["device_id"])
    removed = await remove_soak_devices(db, user.org_id)
    audit.record(db, user, "demo.soak_devices_delete", None, None, {"count": removed}, ip=client_ip(request))
    await db.commit()
    return {"removed": removed}
