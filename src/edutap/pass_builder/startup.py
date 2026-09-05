"""What both applications do before they serve: reconcile the tenants."""

from sqlalchemy.ext.asyncio import async_sessionmaker

from .database import get_engine
from .services.tenants import reconcile_tenants
from .settings import get_settings


async def reconcile_declared_tenants() -> None:
    """Run the reconciliation in its own transaction and commit it.

    Both ASGI applications call this from their lifespan. It is idempotent and
    serialised by an advisory lock, so three render replicas and one UI replica
    starting together do the same work once and read it three times.
    """
    maker = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    async with maker() as session, session.begin():
        await reconcile_tenants(session, get_settings().tenants)
