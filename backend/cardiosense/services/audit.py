"""Audit trail: who did what, to what, and when. Never contains ECG samples."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cardiosense.models import AuditLog, User

_recent_views: dict[tuple[str, str, str], float] = {}
VIEW_AUDIT_INTERVAL_S = 300.0


def record(
    db: AsyncSession,
    actor: User | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    *,
    org_id: str | None = None,
    actor_email: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Add an audit row to the caller's transaction (committed with the action it describes)."""
    row = AuditLog(
        org_id=org_id or (actor.org_id if actor else None),
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else actor_email,
        actor_role=actor.role if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail or {},
        ip=ip,
    )
    db.add(row)
    return row


def record_view(db: AsyncSession, actor: User, target_type: str, target_id: str, ip: str | None = None) -> None:
    """Audit a read access, at most once per actor/target every few minutes (UIs poll)."""
    key = (actor.id, target_type, target_id)
    now = time.monotonic()
    last = _recent_views.get(key)
    if last is not None and now - last < VIEW_AUDIT_INTERVAL_S:
        return
    if len(_recent_views) > 20000:
        _recent_views.clear()
    _recent_views[key] = now
    record(db, actor, f"{target_type}.view", target_type, target_id, ip=ip)


def reset_view_throttle() -> None:
    _recent_views.clear()
