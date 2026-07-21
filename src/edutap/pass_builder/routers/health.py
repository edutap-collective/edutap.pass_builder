"""Liveness and readiness endpoints. No authentication required."""

from collections.abc import Awaitable

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.data_provider import DataProviderClient
from ..clients.objectstore import ObjectStore
from ..database import get_session
from ..dependencies import get_data_provider, get_objectstore
from ..errors import ProblemError

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report that the process is alive."""
    return {"status": "ok"}


async def _check(probe: Awaitable[object]) -> bool:
    """Return whether `probe` completes without raising.

    Used for readiness probes where any failure -- connection refused,
    timeout, a wrapped `ProblemError` -- means only one thing: "not ready".
    The specific exception is not actionable here and is deliberately not
    logged as an error; the caller's `checks` dict already names which probe
    failed.
    """
    try:
        await probe
    except Exception:
        return False
    return True


async def run_readiness_checks(
    session: AsyncSession,
    objectstore: ObjectStore,
    data_provider: DataProviderClient,
) -> dict[str, bool]:
    """Check the database, the object store and the data provider.

    Each check is independent: one failing does not stop the others from
    running, so `/readyz`'s `checks` body always reports the full picture.
    """
    return {
        "database": await _check(session.execute(text("SELECT 1"))),
        "objectstore": await _check(objectstore.ping()),
        "data_provider": await _check(data_provider.fetch_catalogue()),
    }


@router.get("/readyz")
async def readyz(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    objectstore: ObjectStore = Depends(get_objectstore),  # noqa: B008
    data_provider: DataProviderClient = Depends(get_data_provider),  # noqa: B008
) -> dict[str, str]:
    """Report readiness after checking database, object store and data provider."""
    checks = await run_readiness_checks(session, objectstore, data_provider)
    if not all(checks.values()):
        raise ProblemError(503, "not_ready", "Dependencies unavailable", checks=checks)
    return {"status": "ready"}
