"""Pydantic request/response models for the REST API (CONTRACT §4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["patient", "technician", "clinician", "org_admin", "system_admin"]
Tier = Literal["green", "amber", "orange", "red"]
AlertState = Literal["open", "acknowledged", "escalated", "closed"]


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ------------------------------------------------------------------ auth

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: Role
    org_id: str | None
    mfa_enabled: bool
    active: bool = True
    patient_id: str | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None

    @classmethod
    def of(cls, user: Any) -> "UserOut":
        out = cls.model_validate(user)
        out.active = bool(user.is_active)
        return out


class RegisterIn(_In):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    # Supplying org_name creates a new organisation with this account as its org admin.
    # Without it the account is a patient account in the default organisation.
    org_name: str | None = Field(default=None, max_length=200)


class LoginIn(_In):
    email: EmailStr
    password: str = Field(max_length=200)
    otp: str | None = None


class RefreshIn(_In):
    refresh_token: str


class LogoutIn(_In):
    refresh_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class MfaVerifyIn(_In):
    otp: str = Field(min_length=6, max_length=12)


class MfaEnrolOut(BaseModel):
    secret: str
    otpauth_url: str


# ------------------------------------------------------------------ patients

class PatientCreate(_In):
    name: str = Field(min_length=1, max_length=200)
    mrn: str | None = Field(default=None, max_length=64)
    dob: date | None = None
    sex: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=5000)
    consent: bool = False

    @field_validator("mrn", "sex", "notes", "dob", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v


class PatientUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    mrn: str | None = Field(default=None, max_length=64)
    dob: date | None = None
    sex: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=5000)
    consent: bool | None = None

    @field_validator("mrn", "sex", "notes", "dob", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v


class PatientOut(BaseModel):
    id: str
    name: str
    mrn: str | None
    dob: date | None
    sex: str | None
    notes: str | None
    consent: bool
    consent_at: datetime | None = None
    is_demo: bool
    demo_record: str | None = None
    risk_score: int
    risk_tier: Tier
    last_event: dict[str, Any] | None
    signal_status: str
    active_session_id: str | None
    device_id: str | None
    open_alerts: int
    created_at: datetime | None = None


class NoteCreate(_In):
    text: str = Field(min_length=1, max_length=5000)
    kind: Literal["note", "symptom"] = "note"
    session_id: str | None = None
    at_s: float | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    text: str
    kind: str
    author_id: str | None
    author_name: str | None
    session_id: str | None
    at_s: float | None
    created_at: datetime


# ------------------------------------------------------------------ devices

class DeviceCreate(_In):
    device_id: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    name: str | None = Field(default=None, max_length=200)
    patient_id: str | None = None


class DevicePairIn(_In):
    pairing_code: str = Field(min_length=4, max_length=12)
    device_id: str | None = None
    patient_id: str | None = None


class DevicePairOut(BaseModel):
    device_id: str
    device_key: str
    patient_id: str | None


class DeviceOut(BaseModel):
    id: str
    device_id: str
    name: str | None
    patient_id: str | None
    patient_name: str | None
    firmware: str | None
    fs: int | None
    paired: bool
    last_seen: datetime | None
    sqi: float | None
    signal_quality: float | None
    status: str
    task: dict[str, Any] | None
    is_demo: bool
    pairing_code: str | None = None
    pairing_expires_at: datetime | None = None


# ------------------------------------------------------------------ alerts / copilot / demo / admin

class AlertActionIn(_In):
    note: str | None = Field(default=None, max_length=2000)


class CopilotQueryIn(_In):
    patient_id: str
    question: str | None = Field(default=None, max_length=500)


class DemoReplayIn(_In):
    record: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    dataset: str = "MIT-BIH"
    speed: float = Field(default=1.0, ge=0.1, le=60.0)
    patient_id: str | None = None
    start_s: float = Field(default=0.0, ge=0)
    duration_s: float | None = Field(default=None, gt=0)
    loop: bool = False


class DemoStopIn(_In):
    record: str | None = None
    patient_id: str | None = None


class SoakDevicesIn(_In):
    count: int = Field(default=50, ge=1, le=500)


class AdminUserCreate(_In):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: Role
    password: str = Field(min_length=10, max_length=200)
    patient_id: str | None = None


class AdminUserUpdate(_In):
    role: Role | None = None
    active: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    patient_id: str | None = None
    reset_mfa: bool | None = None


class RulesUpdateIn(_In):
    thresholds: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=500)
