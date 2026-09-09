"""Tests for the audit retention sweep and the field-catalogue refresh."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.clients.data_provider import CatalogueField
from edutap.pass_builder.models.db import AuditLog, DataField, Tenant
from edutap.pass_builder.models.enums import ValueType
from edutap.pass_builder.services.retention import (
    purge_expired_audit,
    refresh_catalogue,
)


@pytest.fixture(autouse=True)
async def schema(session):
    """Create every table once per test, in the test's own transaction."""
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


@pytest.fixture
def seed_audit(session):
    """Return a coroutine that inserts one `AuditLog` row with a given `ts`."""

    async def _seed_audit(ts: datetime) -> AuditLog:
        tenant = Tenant(key=f"tenant-{uuid4().hex[:8]}", name="Tenant")
        session.add(tenant)
        await session.flush()
        entry = AuditLog(
            tenant_id=tenant.id,
            ts=ts,
            request_id=f"req-{uuid4().hex[:8]}",
            action="render",
            outcome="success",
            duration_ms=1,
            requested_fields=[],
        )
        session.add(entry)
        await session.flush()
        return entry

    return _seed_audit


class FakeDataProvider:
    """Returns a configured catalogue, same shape as the real HTTP client."""

    def __init__(self, catalogue: list[CatalogueField]) -> None:
        self.catalogue = catalogue

    async def fetch_catalogue(self) -> list[CatalogueField]:
        return self.catalogue


async def test_entries_older_than_retention_are_deleted(session, seed_audit):
    now = datetime(2026, 7, 21, tzinfo=UTC)
    await seed_audit(ts=now - timedelta(days=800))  # older than 24 months
    await seed_audit(ts=now - timedelta(days=10))  # recent
    deleted = await purge_expired_audit(session, retention_months=24, now=now)
    assert deleted == 1


async def test_purge_is_a_no_op_when_nothing_is_expired(session, seed_audit):
    now = datetime(2026, 7, 21, tzinfo=UTC)
    await seed_audit(ts=now - timedelta(days=10))
    deleted = await purge_expired_audit(session, retention_months=24, now=now)
    assert deleted == 0


async def test_refresh_catalogue_loads_the_fetched_fields(session):
    data_provider = FakeDataProvider(
        [
            CatalogueField(key="person.name", kinds=["STRING", "TEXT"]),
            CatalogueField(key="person.birthdate", kinds=["STRING", "DATETIME"]),
        ]
    )

    loaded = await refresh_catalogue(session, data_provider)

    assert loaded == 2
    rows = (await session.execute(select(DataField))).scalars().all()
    assert {row.key for row in rows} == {"person.name", "person.birthdate"}


async def test_refresh_catalogue_replaces_rather_than_duplicates(session):
    data_provider = FakeDataProvider(
        [CatalogueField(key="person.name", kinds=["STRING", "TEXT"])]
    )
    await refresh_catalogue(session, data_provider)

    data_provider.catalogue = [
        CatalogueField(key="person.email", kinds=["STRING", "TEXT", "LINK"])
    ]
    loaded = await refresh_catalogue(session, data_provider)

    assert loaded == 1
    rows = (await session.execute(select(DataField))).scalars().all()
    assert [row.key for row in rows] == ["person.email"]


async def test_refresh_catalogue_keeps_the_kinds_and_falls_back_to_the_key(session):
    """The cache row carries the provider's kinds, so validation can use them.

    It also has to survive a catalogue that names no label: the provider sends
    none at all, and a row whose label is empty would show a blank field list.
    """
    data_provider = FakeDataProvider(
        [CatalogueField(key="pass_valid_until", kinds=["STRING", "TEXT", "DATETIME"])]
    )

    await refresh_catalogue(session, data_provider)

    (row,) = (await session.execute(select(DataField))).scalars().all()
    assert row.kinds == ["STRING", "TEXT", "DATETIME"]
    assert row.value_type is ValueType.TEXT
    assert row.label == "pass_valid_until"
