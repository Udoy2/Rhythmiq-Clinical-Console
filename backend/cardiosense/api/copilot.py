"""Copilot endpoints: cited insight / follow-up question, and stored insights."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_ingest
from cardiosense.db import get_db
from cardiosense.models import Insight, User
from cardiosense.schemas import CopilotQueryIn
from cardiosense.security import CLINICAL_ROLES, STAFF_ROLES, client_ip, load_patient, require_roles
from cardiosense.services import audit
from cardiosense.services.copilot.service import answer, insight_to_dict
from cardiosense.services.ingest import IngestManager

router = APIRouter(tags=["copilot"])


@router.post("/copilot/query")
async def copilot_query(
    body: CopilotQueryIn,
    request: Request,
    user: User = Depends(require_roles(*CLINICAL_ROLES)),
    db: AsyncSession = Depends(get_db),
    ingest: IngestManager = Depends(get_ingest),
) -> dict[str, Any]:
    """A cited, non-diagnostic summary (or answer to ``question``) grounded in the patient's own records."""
    patient = await load_patient(db, user, body.patient_id)
    row = await answer(db, ingest, patient, body.question, user, request.app.state.copilot_llm)
    audit.record(db, user, "copilot.query", "patient", patient.id,
                 {"insight_id": row.id, "generated_by": row.generated_by, "has_question": bool(body.question)},
                 ip=client_ip(request))
    await db.commit()
    return insight_to_dict(row)


@router.get("/patients/{patient_id}/insights")
async def list_insights(
    patient_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    patient = await load_patient(db, user, patient_id)
    rows = await db.execute(
        select(Insight).where(Insight.patient_id == patient.id).order_by(Insight.generated_at.desc()).limit(limit)
    )
    return [insight_to_dict(r) for r in rows.scalars()]
