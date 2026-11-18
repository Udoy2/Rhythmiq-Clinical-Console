"""CardioSense AI backend — FastAPI application.

Run:  cd backend && ~/.venvs/cardiosense/bin/python -m uvicorn cardiosense.main:app --port 8000
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, update

import cardiosense.models  # noqa: F401 - register tables on Base.metadata
from cardiosense import __version__
from cardiosense.api import admin, alerts, auth, copilot, demo, devices, meta, patients, reports, sessions, ws
from cardiosense.config import DISCLAIMER, get_settings
from cardiosense.db import Base, dispose_engine, get_engine, session_factory, utcnow
from cardiosense.models import MonitoringSession, Patient
from cardiosense.services.copilot.service import default_generator
from cardiosense.services.demo import DemoManager
from cardiosense.services.hub import LiveHub
from cardiosense.services.ingest import IngestManager
from cardiosense.services.notify import Notifier
from cardiosense.services.pipeline import PipelineProvider
from cardiosense.services.seed import run_seed
from cardiosense.services.writer import BatchWriter

log = logging.getLogger("cardiosense")

# Idempotent schema adjustments for databases created by an earlier build (R1: no Alembic).
MIGRATIONS = (
    "ALTER TABLE patients ADD COLUMN IF NOT EXISTS demo_record varchar(32)",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS task jsonb",
    "ALTER TABLE monitoring_sessions ADD COLUMN IF NOT EXISTS rule_config_version integer",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrences integer NOT NULL DEFAULT 1",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen_at timestamptz",
)


async def init_database() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in MIGRATIONS:
            await conn.execute(text(statement))
    # Sessions left open by a previous process can no longer be streaming.
    async with session_factory()() as db:
        await db.execute(
            update(MonitoringSession).where(MonitoringSession.ended_at.is_(None)).values(ended_at=utcnow())
        )
        await db.execute(update(Patient).where(Patient.signal_status != "offline").values(signal_status="offline"))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.env != "dev" and settings.jwt_secret.startswith("dev-only"):
        raise RuntimeError("CARDIOSENSE_JWT_SECRET must be set outside dev")
    app.state.started_mono = time.monotonic()
    await init_database()

    pipeline: PipelineProvider = getattr(app.state, "pipeline", None) or PipelineProvider()
    await pipeline.load()
    hub = LiveHub()
    writer = BatchWriter()
    writer.start()
    notifier = Notifier()
    app.state.pipeline = pipeline
    app.state.hub = hub
    app.state.writer = writer
    app.state.notifier = notifier
    app.state.ingest = IngestManager(hub, writer, pipeline, notifier)
    app.state.demo = DemoManager()
    app.state.copilot_llm = default_generator()

    if settings.seed:
        await run_seed(pipeline.rule_version)
    log.info("CardioSense backend %s ready (model_loaded=%s, rule_version=%s, copilot=%s)", __version__,
             pipeline.model_loaded, pipeline.rule_version, "llm" if app.state.copilot_llm else "template")
    try:
        yield
    finally:
        await app.state.demo.shutdown()
        await app.state.ingest.shutdown()
        await notifier.shutdown()
        await writer.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CardioSense AI API",
        version=__version__,
        description=f"Live ECG monitoring, beat labels, rhythm rules, alerts and a grounded copilot. {DISCLAIMER}",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    for module in (auth, patients, devices, sessions, alerts, copilot, reports, demo, admin, meta, ws):
        app.include_router(module.router, prefix="/v1")
    return app


app = create_app()
