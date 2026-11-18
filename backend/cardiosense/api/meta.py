"""Health, metrics and system metadata (model / rule versions, model card, disclaimer)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from cardiosense import __version__
from cardiosense.config import BEAT_LABELS, DEVICE_TASK_RULES, DISCLAIMER, TIERS
from cardiosense.db import session_factory

router = APIRouter(tags=["meta"])

RULE_IDS = (
    "BRADY", "BRADY_SEVERE", "TACHY", "TACHY_SEVERE", "PAUSE", "PAUSE_CRITICAL", "V_COUPLET", "V_RUN", "V_SUSTAINED",
    "BIGEMINY", "TRIGEMINY", "S_RUN", "S_BURDEN", "V_BURDEN", "IRREGULAR",
)

# BRD §8.3 "Known limitations" — shown when model_config.json does not carry its own list.
DEFAULT_LIMITATIONS = [
    "S beats can be missed when they dominate the rhythm; S findings never escalate above Amber alone.",
    "F beats have very low precision on new data; F never triggers an alert alone.",
    "Not yet validated on AD8232 recordings.",
    "Single lead (lead II / MLII): no ischaemia or 12-lead claims.",
    "No AFib-specific model: irregular rhythm is a rule-based flag for review, not a diagnosis.",
]


async def _db_ok() -> bool:
    try:
        async with session_factory()() as db:
            await asyncio.wait_for(db.execute(text("select 1")), timeout=2.0)
        return True
    except Exception:  # noqa: BLE001 - any failure means "not ok"
        return False


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness plus live-path telemetry. Latencies are milliseconds from frame receipt to broadcast."""
    state = request.app.state
    db_ok = await _db_ok()
    pipeline = state.pipeline
    ingest_stats = state.ingest.stats()
    return {
        "status": "ok" if db_ok and pipeline.available else "degraded",
        "version": __version__,
        "uptime_s": round(time.monotonic() - state.started_mono, 1),
        "db": "ok" if db_ok else "unavailable",
        "pipeline": "ok" if pipeline.available else (pipeline.error or "unavailable"),
        "model_loaded": pipeline.model_loaded,
        "model_version": pipeline.model_version,
        "rule_version": pipeline.rule_version,
        "active_sessions": ingest_stats["active_sessions"],
        "latency_ms": ingest_stats["latency_ms"],
        "ingest": {k: v for k, v in ingest_stats.items() if k != "latency_ms"},
        "writer": state.writer.stats(),
        "live": state.hub.stats(),
        "notifications": state.notifier.stats(),
        "demo_replays": state.demo.running_count(),
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Same telemetry as ``/health`` (JSON); kept as a separate path for scrapers."""
    return await health(request)


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    pipeline = request.app.state.pipeline
    cfg: dict[str, Any] = pipeline.model_config or {}
    limitations = cfg.get("limitations") or DEFAULT_LIMITATIONS
    return {
        "app_version": __version__,
        "model_version": pipeline.model_version,
        "rule_version": pipeline.rule_version,
        "model_loaded": pipeline.model_loaded,
        "mode": "model+rules" if pipeline.model_loaded else "rules-only",
        "pipeline_available": pipeline.available,
        "model": cfg or None,
        "metrics": cfg.get("metrics") or cfg.get("evaluation"),
        "evaluation": cfg.get("evaluation"),
        "limitations": limitations,
        "beat_labels": list(BEAT_LABELS),
        "tiers": list(TIERS),
        "rule_ids": list(RULE_IDS),
        "device_task_rules": list(DEVICE_TASK_RULES),
        "disclaimer": DISCLAIMER,
    }
