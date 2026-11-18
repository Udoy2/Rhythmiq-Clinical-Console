"""WebSocket endpoints: device/replay ingest (CONTRACT §2.1) and the live UI feed (CONTRACT §2.2)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from cardiosense.api.deps import ws_ingest
from cardiosense.db import session_factory
from cardiosense.models import Alert, MonitoringSession, Patient
from cardiosense.security import can_view_patient, is_uuid, user_from_token
from cardiosense.services import audit
from cardiosense.services.hub import encode
from cardiosense.services.serialize import alert_to_dict, iso, session_message, session_to_dict

log = logging.getLogger("cardiosense.ws")
router = APIRouter()

CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403


@router.websocket("/stream/{device_id}")
async def stream(ws: WebSocket, device_id: str, key: str = "") -> None:
    """Device / replay sender → server. Auth by device key, consent-gated, one session per connection."""
    await ws.accept()
    await ws_ingest(ws).handle_connection(ws, device_id, key)


@router.websocket("/live/{patient_id}")
async def live(ws: WebSocket, patient_id: str, token: str = "") -> None:
    """Server → UI. Sends the current session and a backlog, then live messages until the client leaves."""
    await ws.accept()
    ingest = ws_ingest(ws)
    async with session_factory()() as db:
        user = await user_from_token(db, token)
        if user is None:
            await ws.close(code=CLOSE_UNAUTHENTICATED, reason="invalid or expired token")
            return
        patient = await db.get(Patient, patient_id) if is_uuid(patient_id) else None
        if patient is None or not can_view_patient(user, patient):
            await ws.close(code=CLOSE_FORBIDDEN, reason="not permitted for this patient")
            return
        alerts = (
            await db.execute(
                select(Alert).where(Alert.patient_id == patient.id, Alert.state != "closed")
                .order_by(Alert.created_at.desc()).limit(50)
            )
        ).scalars().all()
        stored_alerts = {a.id: alert_to_dict(a) for a in reversed(alerts)}
        last_session = None
        if ingest.active_session(patient.id) is None:
            last_session = (
                await db.execute(
                    select(MonitoringSession).where(MonitoringSession.patient_id == patient.id)
                    .order_by(MonitoringSession.started_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
        audit.record_view(db, user, "live", patient.id, ip=ws.client.host if ws.client else None)
        await db.commit()

    # Subscribe and snapshot in the same event-loop step: nothing can be missed or duplicated in between.
    sub = ingest.hub.subscribe(patient.id)
    backlog = ingest.backlog(patient.id)
    if not backlog:
        if last_session is not None:
            backlog.append(encode(session_message(session_to_dict(last_session, active=False))))
        backlog.append(encode({
            "type": "status", "stream": "disconnected",
            "last_sample_time": iso(last_session.ended_at or last_session.started_at) if last_session else None,
        }))
    # Alerts raised moments ago may still be in the write queue: the in-memory copy wins.
    for alert in ingest.open_alerts.get(patient.id, {}).values():
        stored_alerts[alert["id"]] = alert
    backlog.extend(encode({"type": "alert", "alert": a}) for a in stored_alerts.values())

    async def pump() -> None:
        for text in backlog:
            await ws.send_text(text)
        while True:
            text = await sub.next()
            if text is None:  # consumer fell hopelessly behind
                await ws.close(code=1013, reason="consumer too slow")
                return
            await ws.send_text(text)

    sender = asyncio.create_task(pump(), name=f"live-{patient.id[:8]}")
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        ingest.hub.unsubscribe(patient.id, sub)
        sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("live sender ended: %s", type(exc).__name__)
