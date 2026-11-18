"""Organisations, users, refresh-token sessions, audit log, versioned rule configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cardiosense.db import Base, new_id, utcnow

UUID = Uuid(as_uuid=False)
TZ = DateTime(timezone=True)


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    plan: Mapped[str] = mapped_column(String(40), default="demo")
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(UUID, ForeignKey("organisations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # For role=patient: the patient record this account may see (and nothing else).
    patient_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class RefreshToken(Base):
    """One row per issued refresh token; a login creates a family that rotates on every refresh."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(UUID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(UUID, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    used_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_org_time", "org_id", "created_at"),
        Index("ix_audit_actor_time", "actor_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(UUID, nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class RuleConfig(Base):
    """Versioned rhythm-rule thresholds per organisation; the highest version is active."""

    __tablename__ = "rule_configs"
    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_rule_config_org_version"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(UUID, ForeignKey("organisations.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(UUID, nullable=True)
    created_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
