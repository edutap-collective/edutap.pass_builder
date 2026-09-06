"""Reconcile the declared tenants into the table.

The settings are the truth; the table follows. It keeps existing because four
tables hold foreign keys into it, and a key that is only a string in a settings
file is a key nobody checks until the first 500.
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import Tenant
from ..settings import DeclaredTenant

__all__ = ["DeclaredTenant", "reconcile_tenants"]

log = logging.getLogger(__name__)

# One lock for the whole reconciliation. Every replica runs it at startup, and
# two of them starting at once would both find `lmu` missing and both insert
# it -- the second hitting the unique key as an IntegrityError that takes the
# replica down for a race that decided nothing. A transaction-scoped advisory
# lock serialises them; the loser reads what the winner wrote.
_LOCK_KEY = 0x7061737374656E61  # "passtena"


async def reconcile_tenants(
    session: AsyncSession, declared: list[DeclaredTenant]
) -> None:
    """Bring the tenant table in line with the declaration.

    Creates what is declared and missing, overwrites the name of what exists
    (the declaration owns it), reactivates what was declared again, and marks
    inactive what holds rows but is no longer declared -- with a warning, so
    the removal is visible at the one moment somebody is watching a deploy.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})

    existing = {
        row.key: row for row in (await session.execute(select(Tenant))).scalars()
    }
    wanted = {tenant.key: tenant for tenant in declared}

    for key, spec in wanted.items():
        row = existing.get(key)
        if row is None:
            session.add(Tenant(key=key, name=spec.name, active=True))
            log.info("tenant %s created from settings", key)
            continue
        if row.name != spec.name or not row.active:
            row.name = spec.name
            row.active = True

    for key, row in existing.items():
        if key not in wanted and row.active:
            row.active = False
            log.warning(
                "tenant %s is no longer declared in settings and was set "
                "inactive; its rows are kept and its tokens are refused",
                key,
            )

    await session.flush()
