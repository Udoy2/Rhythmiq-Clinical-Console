"""Alerts, notification delivery attempts and copilot insights."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cardiosense.db import Base, new_id, utcnow

UUID = Uuid(as_uuid=False)
TZ = DateTime(timezone=True)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_org_state", "org_id", "state", "created_at"),
        Index("ix_alerts_patient_rule", "patient_id", "rule_id", "state"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(UUID, ForeignKey("organisations.id"))
    patient_id: Mapped[str] = mapped_column(UUID, ForeignKey("patients.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(UUID, nullable=True, index=True)
    rule_id: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="open")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    acknowledged_by_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # display name
    acknowledged_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{state, by, by_id, at, note}] — every workflow transition (also written to the audit log)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rule_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AlertDelivery(Base):
    """One row per notification channel per alert; attempts and outcome are recorded (FR-16)."""

    __tablename__ = "alert_deliveries"

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    alert_id: Mapped[str] = mapped_column(UUID, index=True)
    channel: Mapped[str] = mapped_column(String(16))  # in_app | email | webhook
    target: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # delivered | logged | retrying | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (Index("ix_insights_patient_time", "patient_id", "generated_at"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(UUID, ForeignKey("patients.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    generated_by: Mapped[str] = mapped_column(String(16))  # llm | template
    model: Mapped[str] = mapped_column(String(80))
    guard: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # what the enforcer/filter did
    created_by: Mapped[str | None] = mapped_column(UUID, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
