"""Database engine and session handling (PostgreSQL via asyncpg)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from cardiosense.config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _factory
    if _engine is None:
        s = get_settings()
        if not s.database_url.startswith("postgresql"):
            raise RuntimeError("CardioSense requires PostgreSQL (CARDIOSENSE_DATABASE_URL)")
        _engine = create_async_engine(
            s.database_url, pool_size=s.db_pool_size, max_overflow=10, pool_pre_ping=True
        )
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _factory is not None
    return _factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request; handlers commit explicitly."""
    async with session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
