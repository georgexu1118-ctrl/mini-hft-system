"""
Async SQLAlchemy database setup.

We use SQLAlchemy 2.0 with asyncpg driver. The async session factory is
initialised once at startup and shared via FastAPI's dependency injection.

Connection pooling notes:
  pool_size      — persistent connections kept open (warm, fast to acquire)
  max_overflow   — extra connections allowed during burst traffic
  pool_timeout   — how long to wait for a connection before raising
  pool_recycle   — recycle connections after N seconds (avoids stale TCP)

In production HFT: database writes are on the *cold path* (after matching).
The hot path (matching engine) never touches the DB. Persistence happens
asynchronously from event bus consumers, so DB latency doesn't affect
order processing latency.
"""
from __future__ import annotations
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from infra.config.settings import settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=30,
    pool_recycle=1800,
    echo=settings.api_debug,   # logs SQL in debug mode
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a transactional async DB session.

    Usage:
        @router.post("/orders")
        async def create_order(db: AsyncSession = Depends(get_db_session)):
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables() -> None:
    """Create all tables. Called at startup if not using Alembic migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    """Drop all tables. Used in tests only."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
