"""Database engine and session dependency."""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import get_database_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine."""
    return create_async_engine(
        get_database_settings().async_url, future=True, pool_pre_ping=True
    )


# `AsyncGenerator`, not `AsyncIterator`: typeshed deprecated the
# `asynccontextmanager` overload that takes a function returning the latter, and
# `tests/test_session_commit.py` wraps `get_session` in exactly that. The two
# `lifespan` functions carry the same annotation for the same reason -- FastAPI
# applies `asynccontextmanager` to them itself.
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a session for one request, committing or rolling it back.

    Every write a request makes -- template/credential/publish/sync
    changes, audit entries -- lives in this one session and must reach the
    database exactly once, at request end: on a clean return `commit()`
    persists everything the request did; on an exception `rollback()`
    discards it before the exception continues to propagate, so a failed
    request never leaves partial writes behind. Services only ever
    `flush()`, never `commit()`/`rollback()` -- that is deliberately this
    function's sole responsibility, so every endpoint gets the same
    transaction semantics without repeating them.

    An `outcome="error"` audit entry written while handling an exception
    (see `services/render.py::_write_error_audit` and
    `routers/_lifecycle_audit.py::audited`) would otherwise be discarded by
    the `rollback()` below along with the rest of the failed request --
    both write that entry through `services/audit.py::write_audit_durable`,
    which commits it on its own connection, independent of this session.
    """
    maker = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
