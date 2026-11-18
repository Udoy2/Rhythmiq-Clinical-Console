"""Authentication: register, login, rotating refresh tokens, logout, TOTP MFA (FR-01, FR-02, FR-04)."""

from __future__ import annotations

import secrets
import time
from datetime import timedelta

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.config import get_settings
from cardiosense.db import get_db, new_id, utcnow
from cardiosense.models import Organisation, Patient, RefreshToken, User
from cardiosense.schemas import (
    LoginIn,
    LogoutIn,
    MfaEnrolOut,
    MfaVerifyIn,
    RefreshIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from cardiosense.security import (
    client_ip,
    create_access_token,
    current_user,
    hash_password,
    hash_token,
    new_opaque_token,
    revoke_sid,
    verify_password,
)
from cardiosense.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 8
FAILED_WINDOW_S = 300.0
_failed_logins: dict[str, list[float]] = {}


def _throttled(email: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failed_logins.get(email, []) if now - t < FAILED_WINDOW_S]
    _failed_logins[email] = recent
    return len(recent) >= MAX_FAILED_LOGINS


def _note_failure(email: str) -> None:
    _failed_logins.setdefault(email, []).append(time.monotonic())


def reset_login_throttle() -> None:
    _failed_logins.clear()


async def issue_tokens(db: AsyncSession, user: User, family_id: str | None = None) -> TokenOut:
    """Create an access token and a refresh token in ``family_id`` (a new family = a new login session)."""
    settings = get_settings()
    family = family_id or new_id()
    refresh = new_opaque_token()
    db.add(RefreshToken(
        user_id=user.id, family_id=family, token_hash=hash_token(refresh),
        expires_at=utcnow() + timedelta(days=settings.refresh_ttl_days),
    ))
    return TokenOut(
        access_token=create_access_token(user, family),
        refresh_token=refresh,
        expires_in=settings.access_ttl_min * 60,
        user=UserOut.of(user),
    )


async def _revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    revoke_sid(family_id)


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request, db: AsyncSession = Depends(get_db)) -> TokenOut:
    """Self-registration.

    With ``org_name`` a new organisation is created and the account becomes its org admin. Without it the
    account is a *patient* account in the default organisation with its own (not yet consented) patient
    record. Staff accounts inside an existing organisation are created by its admins (``POST /admin/users``).
    """
    email = body.email.lower()
    if (await db.execute(select(User.id).where(User.email == email))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    password_hash = await hash_password(body.password)
    if body.org_name:
        if (await db.execute(select(Organisation.id).where(Organisation.name == body.org_name))).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "An organisation with this name already exists")
        org = Organisation(id=new_id(), name=body.org_name, plan="trial")
        db.add(org)
        user = User(id=new_id(), org_id=org.id, email=email, name=body.name, role="org_admin",
                    password_hash=password_hash)
    else:
        org = (
            await db.execute(select(Organisation).where(Organisation.name == get_settings().default_org_name))
        ).scalar_one_or_none()
        if org is None:
            org = Organisation(id=new_id(), name=get_settings().default_org_name)
            db.add(org)
        patient = Patient(id=new_id(), org_id=org.id, name=body.name, consent=False)
        db.add(patient)
        user = User(id=new_id(), org_id=org.id, email=email, name=body.name, role="patient",
                    password_hash=password_hash, patient_id=patient.id)
    db.add(user)
    await db.flush()
    tokens = await issue_tokens(db, user)
    audit.record(db, user, "auth.register", "user", user.id, {"role": user.role}, ip=client_ip(request))
    await db.commit()
    return tokens


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    ip = client_ip(request)
    if _throttled(email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed sign-in attempts; try again later")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    ok = user is not None and user.is_active and await verify_password(user.password_hash, body.password)
    if not ok or user is None:
        _note_failure(email)
        audit.record(db, None, "auth.login_failed", "user", user.id if user else None, {"reason": "credentials"},
                     org_id=user.org_id if user else None, actor_email=email, ip=ip)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect e-mail or password")
    if user.mfa_enabled:
        if not body.otp:
            return JSONResponse({"detail": "MFA code required", "mfa_required": True}, status.HTTP_401_UNAUTHORIZED)
        if not _check_otp(user, body.otp):
            _note_failure(email)
            audit.record(db, None, "auth.login_failed", "user", user.id, {"reason": "otp"}, org_id=user.org_id,
                         actor_email=email, ip=ip)
            await db.commit()
            return JSONResponse({"detail": "Invalid MFA code", "mfa_required": True}, status.HTTP_401_UNAUTHORIZED)
    user.last_login_at = utcnow()
    tokens = await issue_tokens(db, user)
    audit.record(db, user, "auth.login", "user", user.id, ip=ip)
    await db.commit()
    _failed_logins.pop(email, None)
    return tokens


def _check_otp(user: User, otp: str) -> bool:
    """Accept a current TOTP code or consume a single-use recovery code."""
    code = otp.strip().replace(" ", "")
    if user.mfa_secret and pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1):
        return True
    hashed = hash_token(code.lower())
    if hashed in (user.recovery_code_hashes or []):
        user.recovery_code_hashes = [h for h in user.recovery_code_hashes if h != hashed]
        return True
    return False


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    """Rotate the refresh token. Re-use of an already rotated token revokes the whole session."""
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
    ).scalar_one_or_none()
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    if row is None:
        raise invalid
    if row.revoked_at is not None or row.used_at is not None:
        await _revoke_family(db, row.family_id)  # token theft / replay: kill the session
        await db.commit()
        raise invalid
    if row.expires_at <= utcnow():
        raise invalid
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid
    row.used_at = row.revoked_at = utcnow()
    tokens = await issue_tokens(db, user, family_id=row.family_id)
    await db.commit()
    return tokens


@router.post("/logout")
async def logout(
    request: Request,
    body: LogoutIn | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke the current session: its refresh tokens stop working and its access token is rejected."""
    sid = getattr(request.state, "sid", None)
    if sid:
        await _revoke_family(db, sid)
    if body and body.refresh_token:
        row = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
        ).scalar_one_or_none()
        if row is not None and row.user_id == user.id:
            await _revoke_family(db, row.family_id)
    audit.record(db, user, "auth.logout", "user", user.id, ip=client_ip(request))
    await db.commit()
    return {"status": "logged_out"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.of(user)


@router.post("/mfa/enrol", response_model=MfaEnrolOut)
async def mfa_enrol(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> MfaEnrolOut:
    """Start TOTP enrolment; MFA becomes active after ``/auth/mfa/verify`` confirms a code."""
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled")
    user.mfa_secret = pyotp.random_base32()
    audit.record(db, user, "auth.mfa_enrol_started", "user", user.id)
    await db.commit()
    url = pyotp.TOTP(user.mfa_secret).provisioning_uri(name=user.email, issuer_name="CardioSense AI")
    return MfaEnrolOut(secret=user.mfa_secret, otpauth_url=url)


@router.post("/mfa/verify")
async def mfa_verify(
    body: MfaVerifyIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    if not user.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start enrolment first (POST /auth/mfa/enrol)")
    if not pyotp.TOTP(user.mfa_secret).verify(body.otp.strip(), valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code")
    recovery_codes: list[str] = []
    if not user.mfa_enabled:
        recovery_codes = [secrets.token_hex(5) for _ in range(8)]
        user.recovery_code_hashes = [hash_token(c) for c in recovery_codes]
        user.mfa_enabled = True
        audit.record(db, user, "auth.mfa_enabled", "user", user.id)
    await db.commit()
    return {"mfa_enabled": True, "recovery_codes": recovery_codes}
