"""Database engine and session dependency."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine."""
    return create_async_engine(
        get_settings().database_url, future=True, pool_pre_ping=True
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for one request."""
    maker = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    async with maker() as session:
        yield session
