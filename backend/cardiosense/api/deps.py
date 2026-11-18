"""Shared FastAPI dependencies and lookups."""

from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.models import MonitoringSession, Patient, User
from cardiosense.security import is_uuid, load_patient
from cardiosense.services.ingest import IngestManager
from cardiosense.services.pipeline import PipelineProvider


def get_ingest(request: Request) -> IngestManager:
    return request.app.state.ingest


def get_pipeline(request: Request) -> PipelineProvider:
    return request.app.state.pipeline


def ws_ingest(ws: WebSocket) -> IngestManager:
    return ws.app.state.ingest


async def load_session(db: AsyncSession, user: User, session_id: str) -> tuple[MonitoringSession, Patient]:
    """A monitoring session the caller may see (through its patient), else 404/403."""
    session = await db.get(MonitoringSession, session_id) if is_uuid(session_id) else None
    if session is None:
        if user.role == "system_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins cannot access patient records")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    try:
        patient = await load_patient(db, user, session.patient_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
        raise
    return session, patient
