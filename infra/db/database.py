"""
Async SQLAlchemy Core database setup.

The execution writer uses Core statements through the asyncpg driver. It does
not construct ORM entities or acquire a connection from matching callbacks.

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

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from infra.config.settings import settings
from infra.db.schema import metadata

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=30,
    pool_recycle=1800,
    echo=settings.api_debug,   # logs SQL in debug mode
)

async def create_tables() -> None:
    """Create Core schema for local development; migrations own production DDL."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def drop_tables() -> None:
    """Drop all tables. Used in tests only."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
