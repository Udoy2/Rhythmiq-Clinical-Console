"""Password hashing (Argon2id), tokens, request authentication and RBAC helpers (BRD §7.3)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.config import get_settings
from cardiosense.db import get_db, new_id, utcnow
from cardiosense.models import Patient, User

_hasher = PasswordHasher()  # argon2-cffi default profile is Argon2id
_bearer = HTTPBearer(auto_error=False)
JWT_ALG = "HS256"

STAFF_ROLES = ("technician", "clinician", "org_admin")
CLINICAL_ROLES = ("clinician", "org_admin")
ADMIN_ROLES = ("org_admin", "system_admin")


# ---------------------------------------------------------------- passwords / secrets

def _hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def _verify_password_sync(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    # Argon2 is deliberately slow: keep it off the event loop so live streams are not stalled.
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, password_hash, password)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def device_key_hash(key: str) -> str:
    pepper = get_settings().device_key_pepper.encode()
    return hmac.new(pepper, key.encode(), hashlib.sha256).hexdigest()


def verify_device_key(key: str, key_hash: str | None) -> bool:
    return bool(key_hash) and hmac.compare_digest(device_key_hash(key), key_hash or "")


# ---------------------------------------------------------------- access tokens

# Session ids (refresh-token families) revoked by logout; entries expire with the access-token TTL.
_revoked_sids: dict[str, float] = {}


def revoke_sid(sid: str) -> None:
    now = time.time()
    for k in [k for k, exp in _revoked_sids.items() if exp < now]:
        _revoked_sids.pop(k, None)
    _revoked_sids[sid] = now + get_settings().access_ttl_min * 60 + 60


def create_access_token(user: User, sid: str) -> str:
    s = get_settings()
    now = utcnow()
    payload = {
        "sub": user.id,
        "role": user.role,
        "org": user.org_id,
        "sid": sid,
        "typ": "access",
        "jti": new_id(),
        "iat": now,
        "exp": now + timedelta(minutes=s.access_ttl_min),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any]:
    """Return the claims or raise ``jwt.PyJWTError``."""
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    if claims.get("sid") in _revoked_sids:
        raise jwt.InvalidTokenError("session revoked")
    return claims


async def user_from_token(db: AsyncSession, token: str) -> User | None:
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    user = await db.get(User, claims["sub"])
    if user is None or not user.is_active:
        return None
    return user


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token", {"WWW-Authenticate": "Bearer"})
    user = await db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled or removed")
    request.state.sid = claims.get("sid")
    return user


def require_roles(*roles: str):
    """Dependency factory: the caller must hold one of ``roles``."""

    async def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not permit this action")
        return user

    return dep


# ---------------------------------------------------------------- patient scoping

def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def can_view_patient(user: User, patient: Patient) -> bool:
    """Patients see only themselves; technicians, clinicians and org admins see their organisation.

    System admins operate the service but have no access to patient records (BRD §7.3).
    """
    if user.role == "patient":
        return user.patient_id == patient.id
    if user.role in STAFF_ROLES:
        return user.org_id == patient.org_id
    return False


async def load_patient(db: AsyncSession, user: User, patient_id: str) -> Patient:
    """Fetch a patient the caller may see, else 403 (own role scope) / 404 (unknown or other org)."""
    if user.role == "system_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins cannot access patient records")
    if user.role == "patient" and user.patient_id != patient_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Patients can only access their own record")
    patient = await db.get(Patient, patient_id) if is_uuid(patient_id) else None
    if patient is None or not can_view_patient(user, patient):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
