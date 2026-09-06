"""The tenant list is declared in settings and reconciled into the database.

A tenant is deployment topology, not runtime data: three of them, changing
almost never, and already named by the permission map. Two places holding one
list is how the lists drift -- so the settings are the truth and the table
follows, keeping the foreign keys that protect the rows underneath.
"""

import logging

import pytest
from sqlalchemy import select
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.services.tenants import DeclaredTenant, reconcile_tenants


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


async def all_tenants(session) -> dict[str, Tenant]:
    rows = (await session.execute(select(Tenant))).scalars().all()
    return {row.key: row for row in rows}


async def test_a_declared_tenant_that_is_missing_is_created(session):
    await reconcile_tenants(session, [DeclaredTenant(key="lmu", name="LMU München")])
    rows = await all_tenants(session)
    assert rows["lmu"].name == "LMU München"
    assert rows["lmu"].active is True


async def test_reconciling_twice_changes_nothing(session):
    """Every replica runs this at startup; the second run must be a no-op."""
    declared = [DeclaredTenant(key="lmu", name="LMU München")]
    await reconcile_tenants(session, declared)
    first = (await all_tenants(session))["lmu"].id
    await reconcile_tenants(session, declared)
    rows = await all_tenants(session)
    assert len(rows) == 1
    assert rows["lmu"].id == first, "the row was replaced, and every FK with it"


async def test_the_declaration_owns_the_name(session):
    """The settings are the truth; a name edited elsewhere is overwritten."""
    session.add(Tenant(key="lmu", name="typo"))
    await session.flush()
    await reconcile_tenants(session, [DeclaredTenant(key="lmu", name="LMU München")])
    assert (await all_tenants(session))["lmu"].name == "LMU München"


async def test_an_undeclared_tenant_with_rows_is_deactivated_not_deleted(
    session, caplog
):
    """Q26: inactive, with a warning. Never deleted, never silently kept.

    Deleting would take four tables of rows with it on a typo in a settings
    file. Keeping it active would let a tenant somebody removed on purpose go
    on issuing passes. Inactive is the one state that avoids both, and the
    audit trail stays readable.
    """
    session.add(Tenant(key="stwm", name="Studierendenwerk"))
    await session.flush()
    with caplog.at_level(logging.WARNING):
        await reconcile_tenants(session, [DeclaredTenant(key="lmu", name="LMU")])
    rows = await all_tenants(session)
    assert rows["stwm"].active is False, "deactivated, not deleted"
    assert "stwm" in caplog.text


async def test_a_redeclared_tenant_comes_back(session):
    session.add(Tenant(key="stwm", name="Studierendenwerk", active=False))
    await session.flush()
    await reconcile_tenants(session, [DeclaredTenant(key="stwm", name="STWM")])
    assert (await all_tenants(session))["stwm"].active is True
