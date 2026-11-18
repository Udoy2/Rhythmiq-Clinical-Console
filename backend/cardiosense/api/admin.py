"""Admin console: users and roles, versioned alert-rule thresholds, audit search (FR-21)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.api.deps import get_pipeline
from cardiosense.db import get_db, new_id, utcnow
from cardiosense.models import AuditLog, Patient, RefreshToken, RuleConfig, User
from cardiosense.schemas import AdminUserCreate, AdminUserUpdate, RulesUpdateIn, UserOut
from cardiosense.security import ADMIN_ROLES, client_ip, hash_password, is_uuid, require_roles
from cardiosense.services import audit
from cardiosense.services.pipeline import PipelineProvider
from cardiosense.services.serialize import iso

router = APIRouter(tags=["admin"])

# Used only if the pipeline core does not expose its defaults (BRD §8.4 R1 values).
FALLBACK_RULES: dict[str, Any] = {
    "brady_bpm": 50.0, "brady_severe_bpm": 40.0, "tachy_bpm": 100.0, "tachy_severe_bpm": 150.0,
    "pause_s": 2.0, "pause_critical_s": 3.0, "v_run_min_beats": 3, "v_sustained_s": 30.0,
    "v_sustained_bpm": 100.0, "geminy_min_cycles": 6, "s_run_min_beats": 3, "s_run_bpm": 100.0,
    "burden_amber_pct": 1.0, "burden_orange_pct": 10.0, "irregular_ratio": 0.15, "sqi_low": 0.5,
}


# ------------------------------------------------------------------ users

async def _load_user(db: AsyncSession, admin: User, user_id: str) -> User:
    target = await db.get(User, user_id) if is_uuid(user_id) else None
    if target is None or (admin.role == "org_admin" and target.org_id != admin.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return target


@router.get("/admin/users", response_model=list[UserOut])
async def list_users(
    admin: User = Depends(require_roles(*ADMIN_ROLES)), db: AsyncSession = Depends(get_db)
) -> list[UserOut]:
    stmt = select(User).order_by(User.email)
    if admin.role == "org_admin":
        stmt = stmt.where(User.org_id == admin.org_id)
    return [UserOut.of(u) for u in (await db.execute(stmt)).scalars()]


@router.post("/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Create an account in the admin's organisation (the R1 stand-in for e-mail invites)."""
    if body.role == "system_admin" and admin.role != "system_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a system admin can create system admins")
    email = body.email.lower()
    if (await db.execute(select(User.id).where(User.email == email))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    patient_id = await _checked_patient_link(db, admin, body.role, body.patient_id)
    user = User(id=new_id(), org_id=admin.org_id, email=email, name=body.name, role=body.role,
                password_hash=await hash_password(body.password), patient_id=patient_id)
    db.add(user)
    audit.record(db, admin, "admin.user_create", "user", user.id, {"role": body.role}, ip=client_ip(request))
    await db.commit()
    return UserOut.of(user)


async def _checked_patient_link(db: AsyncSession, admin: User, role: str, patient_id: str | None) -> str | None:
    if role != "patient" or patient_id is None:
        return None
    patient = await db.get(Patient, patient_id) if is_uuid(patient_id) else None
    if patient is None or patient.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient.id


@router.patch("/admin/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    body: AdminUserUpdate,
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    target = await _load_user(db, admin, user_id)
    changes: dict[str, Any] = {}
    if body.role is not None and body.role != target.role:
        if admin.role != "system_admin" and "system_admin" in (body.role, target.role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a system admin can grant or remove system admin")
        if target.id == admin.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        changes["role"] = {"from": target.role, "to": body.role}
        target.role = body.role
        if body.role != "patient":
            target.patient_id = None
    if body.active is not None and body.active != target.is_active:
        if target.id == admin.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")
        changes["active"] = body.active
        target.is_active = body.active
        if not body.active:  # deactivation ends every session of the account
            await db.execute(
                update(RefreshToken).where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
    if body.name is not None and body.name != target.name:
        changes["name"] = True
        target.name = body.name
    if body.patient_id is not None and target.role == "patient":
        target.patient_id = await _checked_patient_link(db, admin, "patient", body.patient_id)
        changes["patient_id"] = target.patient_id
    if body.reset_mfa:
        target.mfa_enabled, target.mfa_secret, target.recovery_code_hashes = False, None, []
        changes["reset_mfa"] = True
    if changes:
        audit.record(db, admin, "admin.user_update", "user", target.id, changes, ip=client_ip(request))
    await db.commit()
    return UserOut.of(target)


# ------------------------------------------------------------------ rules

def _defaults(pipeline: PipelineProvider) -> dict[str, Any]:
    return dict(pipeline.default_rules or FALLBACK_RULES)


def _validate_rules(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overrides`` onto ``defaults``; reject unknown keys and values of the wrong kind."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in defaults.items()}

    def check_number(path: str, value: Any, like: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise HTTPException(422, f"{path} must be a non-negative number")
        if isinstance(like, int) and not isinstance(like, bool):
            if int(value) != value:
                raise HTTPException(422, f"{path} must be a whole number")
            return int(value)
        return float(value)

    for key, value in overrides.items():
        if key not in defaults:
            raise HTTPException(422, f"Unknown rule threshold: {key}")
        default = defaults[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise HTTPException(422, f"{key} must be an object")
            for sub, sub_value in value.items():
                if sub not in default:
                    raise HTTPException(422, f"Unknown key {key}.{sub}")
                merged[key][sub] = check_number(f"{key}.{sub}", sub_value, default[sub])
        else:
            merged[key] = check_number(key, value, default)
    try:  # let the core apply its own validation as well, when it is importable
        from cardiosense.core.config import RulesConfig  # noqa: PLC0415

        RulesConfig.from_dict(merged)
    except ImportError:
        pass
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Invalid rule configuration: {exc}") from None
    return merged


async def _rules_out(db: AsyncSession, org_id: str, pipeline: PipelineProvider) -> dict[str, Any]:
    rows = (
        await db.execute(select(RuleConfig).where(RuleConfig.org_id == org_id).order_by(RuleConfig.version.desc()))
    ).scalars().all()
    defaults = _defaults(pipeline)
    current = rows[0] if rows else None
    config = {**defaults, **current.config} if current else defaults
    return {
        "version": current.version if current else 0,
        "rule_version": pipeline.rule_version,
        "thresholds": config,
        "config": config,
        "defaults": defaults,
        "is_default": current is None or config == defaults,
        "updated_at": iso(current.created_at) if current else None,
        "updated_by": current.created_by_email if current else None,
        "note": current.note if current else None,
        "applies_to": "sessions started after the change",
        "history": [
            {"version": r.version, "updated_at": iso(r.created_at), "updated_by": r.created_by_email, "note": r.note}
            for r in rows
        ],
    }


@router.get("/admin/rules")
async def get_rules(
    user: User = Depends(require_roles("clinician", *ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
    pipeline: PipelineProvider = Depends(get_pipeline),
) -> dict[str, Any]:
    """Active rule thresholds of the caller's organisation (clinicians: read-only)."""
    return await _rules_out(db, user.org_id, pipeline)


@router.put("/admin/rules")
async def put_rules(
    body: RulesUpdateIn,
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
    pipeline: PipelineProvider = Depends(get_pipeline),
) -> dict[str, Any]:
    """Store a new version of the thresholds. Earlier versions are kept; running sessions are unaffected."""
    overrides = body.thresholds if body.thresholds is not None else body.config
    if overrides is None:
        raise HTTPException(422, "Send the thresholds to store")
    defaults = _defaults(pipeline)
    config = _validate_rules(defaults, overrides)
    latest = (
        await db.execute(
            select(RuleConfig).where(RuleConfig.org_id == admin.org_id).order_by(RuleConfig.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    previous = {**defaults, **latest.config} if latest else defaults
    changed = {k: {"from": previous.get(k), "to": v} for k, v in config.items() if previous.get(k) != v}
    if changed:
        version = (latest.version if latest else 0) + 1
        db.add(RuleConfig(id=new_id(), org_id=admin.org_id, version=version, config=config, note=body.note,
                          created_by=admin.id, created_by_email=admin.email))
        audit.record(db, admin, "admin.rules_update", "rule_config", str(version), {"changed": changed},
                     ip=client_ip(request))
        await db.commit()
    return await _rules_out(db, admin.org_id, pipeline)


# ------------------------------------------------------------------ audit

def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@router.get("/audit")
async def search_audit(
    actor: str | None = Query(default=None, max_length=320, description="actor e-mail (substring) or user id"),
    action: str | None = Query(default=None, max_length=60, description="action or prefix, e.g. alert. or auth.login"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    target_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(require_roles("patient", "clinician", *ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Audit search. Org admins see their organisation, system admins everything; clinicians see their own
    actions; a patient sees their own actions and every access to their record."""
    stmt = select(AuditLog)
    if user.role == "org_admin":
        stmt = stmt.where(AuditLog.org_id == user.org_id)
    elif user.role == "clinician":
        stmt = stmt.where(AuditLog.actor_id == user.id)
    elif user.role == "patient":
        own_record = and_(AuditLog.target_type.in_(("patient", "live")), AuditLog.target_id == (user.patient_id or ""))
        stmt = stmt.where(or_(AuditLog.actor_id == user.id, own_record))
    if actor:
        if is_uuid(actor):
            stmt = stmt.where(AuditLog.actor_id == actor)
        else:
            stmt = stmt.where(AuditLog.actor_email.ilike(f"%{actor.strip()}%"))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action.strip()}%"))
    if from_:
        stmt = stmt.where(AuditLog.created_at >= _aware(from_))
    if to:
        stmt = stmt.where(AuditLog.created_at <= _aware(to))
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    rows = await db.execute(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit))
    return [
        {"id": str(r.id), "created_at": iso(r.created_at), "at": iso(r.created_at), "actor_id": r.actor_id,
         "actor_email": r.actor_email, "actor_role": r.actor_role, "action": r.action, "target_type": r.target_type,
         "target_id": r.target_id, "detail": r.detail or {}, "ip": r.ip}
        for r in rows.scalars()
    ]
