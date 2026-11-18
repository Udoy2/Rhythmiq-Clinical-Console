"""Devices and pairing (FR-06). Pairing codes expire after 10 minutes; device keys are stored hashed."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_ingest
from cardiosense.config import get_settings
from cardiosense.db import get_db, utcnow
from cardiosense.models import Device, Patient, User
from cardiosense.schemas import DeviceCreate, DeviceOut, DevicePairIn, DevicePairOut
from cardiosense.security import client_ip, device_key_hash, is_uuid, load_patient, require_roles
from cardiosense.services import audit
from cardiosense.services.ingest import IngestManager

router = APIRouter(tags=["devices"])

VIEW_ROLES = ("patient", "technician", "clinician", "org_admin", "system_admin")
MANAGE_ROLES = ("patient", "technician", "org_admin", "system_admin")  # patient: own device only
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def _device_out(d: Device, patient_name: str | None, ingest: IngestManager, *, show_code: bool = False) -> DeviceOut:
    streaming = d.id in ingest.sessions
    paired = d.key_hash is not None and d.patient_id is not None
    code_valid = d.pairing_code is not None and d.pairing_expires_at is not None and d.pairing_expires_at > utcnow()
    if streaming:
        state = "streaming"
    elif paired:
        state = "paired"
    elif code_valid:
        state = "pairing"
    else:
        state = "unpaired"
    return DeviceOut(
        id=d.id, device_id=d.id, name=d.name, patient_id=d.patient_id, patient_name=patient_name,
        firmware=d.firmware, fs=d.fs, paired=paired, last_seen=d.last_seen, sqi=d.sqi, signal_quality=d.sqi,
        status=state, task=d.task if streaming else None, is_demo=d.is_demo,
        pairing_code=d.pairing_code if show_code and code_valid else None,
        pairing_expires_at=d.pairing_expires_at if show_code and code_valid else None,
    )


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    user: User = Depends(require_roles(*VIEW_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> list[DeviceOut]:
    stmt = select(Device, Patient.name).outerjoin(Patient, Patient.id == Device.patient_id).order_by(Device.id)
    if user.role == "patient":
        stmt = stmt.where(Device.patient_id == user.patient_id)
    elif user.role != "system_admin":
        stmt = stmt.where(Device.org_id == user.org_id)
    can_see_codes = user.role in MANAGE_ROLES
    hide_names = user.role == "system_admin"  # system admins manage hardware, not patient records
    return [
        _device_out(d, None if hide_names else name, ingest, show_code=can_see_codes)
        for d, name in await db.execute(stmt)
    ]


@router.post("/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceCreate,
    request: Request,
    user: User = Depends(require_roles(*MANAGE_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> DeviceOut:
    """Register a device (or re-issue a code for an existing one) and return a 10-minute pairing code."""
    patient: Patient | None = None
    patient_id = user.patient_id if user.role == "patient" else body.patient_id
    if patient_id:
        if user.role == "system_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins cannot assign devices to patients")
        patient = await load_patient(db, user, patient_id)
    device_id = body.device_id or f"dev-{secrets.token_hex(4)}"
    device = await db.get(Device, device_id)
    if device is not None:
        own = device.patient_id == user.patient_id if user.role == "patient" else device.org_id == user.org_id
        if not own and user.role != "system_admin":
            raise HTTPException(status.HTTP_409_CONFLICT, "This device id is already registered")
        if device.id in ingest.sessions:
            raise HTTPException(status.HTTP_409_CONFLICT, "Device is streaming; stop the stream before re-pairing")
    else:
        device = Device(id=device_id, org_id=user.org_id)
        db.add(device)
    if patient is not None and device.is_demo and not patient.is_demo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Demo devices cannot be paired to real patients")
    if body.name:
        device.name = body.name
    if patient is not None:
        device.patient_id = patient.id
    device.pairing_code = _new_code()
    device.pairing_expires_at = utcnow() + timedelta(minutes=get_settings().pairing_code_ttl_min)
    audit.record(db, user, "device.register", "device", device.id, {"patient_id": device.patient_id},
                 ip=client_ip(request))
    await db.commit()
    return _device_out(device, patient.name if patient else None, ingest, show_code=True)


@router.post("/devices/pair", response_model=DevicePairOut)
async def pair_device(
    body: DevicePairIn,
    request: Request,
    user: User = Depends(require_roles(*MANAGE_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> DevicePairOut:
    """Redeem a pairing code: binds the device to the patient and returns its key (shown once)."""
    stmt = select(Device).where(Device.pairing_code == body.pairing_code.strip().upper())
    if body.device_id:
        stmt = stmt.where(Device.id == body.device_id)
    if user.role == "patient":
        stmt = stmt.where(Device.patient_id == user.patient_id)
    elif user.role != "system_admin":
        stmt = stmt.where(Device.org_id == user.org_id)
    device = (await db.execute(stmt)).scalars().first()
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pairing code")
    if device.pairing_expires_at is None or device.pairing_expires_at <= utcnow():
        device.pairing_code = device.pairing_expires_at = None
        await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pairing code expired; request a new one")
    patient_id = body.patient_id or device.patient_id
    if not patient_id or not is_uuid(patient_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No patient selected for this device")
    patient = await db.get(Patient, patient_id)
    if patient is None or patient.org_id != device.org_id or (user.role == "patient" and patient.id != user.patient_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if device.is_demo and not patient.is_demo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Demo devices cannot be paired to real patients")
    if device.id in ingest.sessions:
        raise HTTPException(status.HTTP_409_CONFLICT, "Device is streaming; stop the stream before re-pairing")
    key = secrets.token_urlsafe(32)
    device.key_hash = device_key_hash(key)
    device.patient_id = patient.id
    device.paired_at = utcnow()
    device.pairing_code = device.pairing_expires_at = None  # single use
    audit.record(db, user, "device.pair", "device", device.id, {"patient_id": patient.id}, ip=client_ip(request))
    await db.commit()
    return DevicePairOut(device_id=device.id, device_key=key, patient_id=patient.id)
