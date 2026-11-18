"""Report download: ``GET /v1/reports/{patient_id}.pdf?range=``."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_pipeline
from cardiosense.api.meta import DEFAULT_LIMITATIONS
from cardiosense.db import get_db, utcnow
from cardiosense.models import Alert, MonitoringSession, User
from cardiosense.security import client_ip, current_user, load_patient
from cardiosense.services import audit
from cardiosense.services.pipeline import PipelineProvider
from cardiosense.services.reports import build_patient_report
from cardiosense.services.serialize import alert_to_dict, session_to_dict
from cardiosense.services.trends import summarise, trend_buckets, window_bounds

router = APIRouter(tags=["reports"])

# range -> (bucket seconds, column title) for the per-period table of the report
REPORT_BUCKETS = {"24h": (3 * 3600, "3-hour period"), "7d": (86400, "Day"), "14d": (86400, "Day"),
                  "30d": (86400, "Day"), "90d": (7 * 86400, "Week from")}


@router.get("/reports/{patient_id}.pdf")
async def patient_report(
    patient_id: str,
    request: Request,
    range: Literal["24h", "7d", "14d", "30d", "90d"] = Query(default="7d"),  # noqa: A002 - contract name
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    pipeline: PipelineProvider = Depends(get_pipeline),
) -> Response:
    patient = await load_patient(db, user, patient_id)
    start, end, _ = window_bounds(range)
    bucket_s, bucket_label = REPORT_BUCKETS[range]
    buckets, gaps = await trend_buckets(db, patient.id, start, end, bucket_s)
    alerts = (
        await db.execute(
            select(Alert).where(Alert.patient_id == patient.id, Alert.created_at >= start, Alert.created_at < end)
            .order_by(Alert.created_at.desc()).limit(200)
        )
    ).scalars().all()
    sessions = (
        await db.execute(
            select(MonitoringSession)
            .where(MonitoringSession.patient_id == patient.id, MonitoringSession.started_at >= start,
                   MonitoringSession.started_at < end)
            .order_by(MonitoringSession.started_at.desc())
        )
    ).scalars().all()
    data = {
        "patient": {"name": patient.name, "mrn": patient.mrn, "dob": patient.dob, "sex": patient.sex,
                    "is_demo": patient.is_demo},
        "range": range, "start": start, "end": end, "generated_at": utcnow(), "generated_by": user.name,
        "versions": {
            "models": sorted({s.model_version for s in sessions if s.model_version}),
            "rules": sorted({s.rule_version for s in sessions if s.rule_version}),
            "current_model": pipeline.model_version, "current_rules": pipeline.rule_version,
        },
        "summary": summarise(buckets), "daily": buckets, "bucket_label": bucket_label, "gaps": gaps,
        "alerts": [alert_to_dict(a, with_evidence=False) for a in alerts],
        "sessions": [session_to_dict(s) for s in sessions],
        "limitations": (pipeline.model_config or {}).get("limitations") or DEFAULT_LIMITATIONS,
    }
    audit.record(db, user, "report.export_pdf", "patient", patient.id, {"range": range}, ip=client_ip(request))
    await db.commit()
    pdf = await asyncio.to_thread(build_patient_report, data)
    filename = f"cardiosense-report-{patient.id[:8]}-{range}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
