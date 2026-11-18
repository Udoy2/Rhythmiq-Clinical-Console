"""Monitoring sessions and their time series: beats, rhythm windows, signal chunks, gaps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cardiosense.db import Base, new_id, utcnow

UUID = Uuid(as_uuid=False)
TZ = DateTime(timezone=True)


class MonitoringSession(Base):
    __tablename__ = "monitoring_sessions"
    __table_args__ = (Index("ix_sessions_patient_started", "patient_id", "started_at"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(UUID, ForeignKey("patients.id", ondelete="CASCADE"))
    device_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16))  # serial | replay | seed (generated demo history)
    record: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dataset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fs: Mapped[int] = mapped_column(Integer)
    adc_zero: Mapped[float | None] = mapped_column(Float, nullable=True)
    adc_gain: Mapped[float | None] = mapped_column(Float, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_loaded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_sample: Mapped[int] = mapped_column(BigInteger, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Beat(Base):
    __tablename__ = "beats"
    __table_args__ = (Index("ix_beats_session_sample", "session_id", "sample"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(UUID, ForeignKey("monitoring_sessions.id", ondelete="CASCADE"))
    sample: Mapped[int] = mapped_column(BigInteger)
    time_s: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    probs: Mapped[dict[str, float] | None] = mapped_column(JSONB, nullable=True)
    truth: Mapped[str | None] = mapped_column(String(8), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RhythmWindow(Base):
    __tablename__ = "rhythm_windows"
    __table_args__ = (
        Index("ix_windows_session_start", "session_id", "start_s"),
        Index("ix_windows_patient_t", "patient_id", "t"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(UUID, ForeignKey("monitoring_sessions.id", ondelete="CASCADE"))
    patient_id: Mapped[str] = mapped_column(UUID)
    t: Mapped[datetime] = mapped_column(TZ, default=utcnow)  # wall-clock time the window was produced
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    hr_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sdnn_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmssd_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    s_burden_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    v_burden_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sqi: Mapped[float | None] = mapped_column(Float, nullable=True)
    beat_counts: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_tier: Mapped[str] = mapped_column(String(10), default="green")
    rule_version: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SignalChunk(Base):
    """~1 s of signal as little-endian float32. kind: "display" (filtered mV) or "raw" (device units)."""

    __tablename__ = "signal_chunks"
    __table_args__ = (Index("ix_signal_session_kind_t0", "session_id", "kind", "t0"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(UUID, ForeignKey("monitoring_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))
    t0: Mapped[int] = mapped_column(BigInteger)
    n: Mapped[int] = mapped_column(Integer)
    fs: Mapped[int] = mapped_column(Integer)
    lead_off: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[bytes] = mapped_column(LargeBinary)


class SessionGap(Base):
    __tablename__ = "session_gaps"
    __table_args__ = (Index("ix_gaps_session", "session_id", "at_sample"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(UUID, ForeignKey("monitoring_sessions.id", ondelete="CASCADE"))
    at_sample: Mapped[int] = mapped_column(BigInteger)
    missing: Mapped[int] = mapped_column(BigInteger)
