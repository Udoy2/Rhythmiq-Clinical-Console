"""Patients, clinical notes and devices."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cardiosense.db import Base, new_id, utcnow

UUID = Uuid(as_uuid=False)
TZ = DateTime(timezone=True)


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (Index("ix_patients_org_risk", "org_id", "risk_score"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(UUID, ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mrn: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    # Demo / replay patients are flagged and never mixed with real patient records.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_record: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_tier: Mapped[str] = mapped_column(String(10), default="green")
    # {label, tier, rule_id, time} of the most recent alert or notable finding
    last_event: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # "offline" when no stream; otherwise the live quality state: streaming | lead_off | low_sqi | gap
    signal_status: Mapped[str] = mapped_column(String(16), default="offline")
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class ClinicalNote(Base):
    __tablename__ = "clinical_notes"
    __table_args__ = (Index("ix_notes_patient_time", "patient_id", "created_at"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(UUID, ForeignKey("patients.id", ondelete="CASCADE"))
    author_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="note")  # note | symptom
    text: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    at_s: Mapped[float | None] = mapped_column(Float, nullable=True)  # optional episode annotation time
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # e.g. "dev-demo-208"
    org_id: Mapped[str] = mapped_column(UUID, ForeignKey("organisations.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("patients.id", ondelete="SET NULL"), index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_code: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    paired_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    sqi: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Open device task raised instead of a clinical alert: {rule_id, title, detail, time} (LEAD_OFF / LOW_SQI)
    task: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
