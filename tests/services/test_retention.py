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


# --- ein Katalog je View ----------------------------------------------------------


class ViewAwareFakeProvider:
    """Serves a different catalogue per view, and records which views were asked for.

    The whole point of the change under test: one deployment reads several views, and
    what a mapping rule is validated against has to be the catalogue of the view ITS
    template reads -- not whichever one the deployment happens to default to.
    """

    def __init__(self, by_view: dict[str | None, list[CatalogueField]]) -> None:
        self.by_view = by_view
        self.asked: list[str | None] = []

    def for_view(self, view_type: str | None) -> "ViewAwareFakeProvider":
        self.asked.append(view_type)
        self._current = view_type
        return self

    async def fetch_catalogue(self) -> list[CatalogueField]:
        return self.by_view[self._current]


MENSAPASS = [
    CatalogueField(key="role", kinds=["STRING", "TEXT"]),
    CatalogueField(key="role_valid_until", kinds=["STRING", "DATETIME"]),
]
FULL_VIEW = [
    CatalogueField(key="given_name", kinds=["STRING", "TEXT"]),
    CatalogueField(key="surname", kinds=["STRING", "TEXT"]),
]


async def test_a_cached_field_remembers_which_view_it_came_from(session):
    """Without this column the cache is one flat namespace for the whole deployment.

    That is the defect: `Template.view_type` gives every template its own view, but
    the field cache stayed deployment-wide and single-view. A mensapass mapping rule
    was validated against `full_view` and answered `422 unknown field` -- for a field
    the provider offers, in the view the template actually reads.
    """
    provider = ViewAwareFakeProvider({"mensapass": MENSAPASS})
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )

    rows = (await session.execute(select(DataField))).scalars().all()
    assert {row.view_type for row in rows} == {"mensapass"}


async def test_two_views_live_side_by_side(session):
    """The same key may exist in two views, and they must not overwrite each other."""
    provider = ViewAwareFakeProvider({"mensapass": MENSAPASS, "full_view": FULL_VIEW})
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )
    await refresh_catalogue(
        session, provider.for_view("full_view"), view_type="full_view"
    )

    rows = (await session.execute(select(DataField))).scalars().all()
    assert {(row.view_type, row.key) for row in rows} == {
        ("mensapass", "role"),
        ("mensapass", "role_valid_until"),
        ("full_view", "given_name"),
        ("full_view", "surname"),
    }


async def test_refreshing_one_view_leaves_the_others_alone(session):
    """A refresh replaces ITS view, not the cache.

    Deleting everything would make refreshing one view silently empty the others, and
    the next mapping rule against them would fail with `unknown field` -- for a field
    nobody touched.
    """
    provider = ViewAwareFakeProvider({"mensapass": MENSAPASS, "full_view": FULL_VIEW})
    await refresh_catalogue(
        session, provider.for_view("full_view"), view_type="full_view"
    )
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )

    provider.by_view["mensapass"] = [CatalogueField(key="role", kinds=["STRING"])]
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )

    rows = (await session.execute(select(DataField))).scalars().all()
    assert {(row.view_type, row.key) for row in rows} == {
        ("mensapass", "role"),
        ("full_view", "given_name"),
        ("full_view", "surname"),
    }


async def test_a_field_the_provider_stopped_offering_disappears_from_its_view(session):
    provider = ViewAwareFakeProvider({"mensapass": MENSAPASS})
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )

    provider.by_view["mensapass"] = [CatalogueField(key="role", kinds=["STRING"])]
    await refresh_catalogue(
        session, provider.for_view("mensapass"), view_type="mensapass"
    )

    rows = (await session.execute(select(DataField))).scalars().all()
    assert [row.key for row in rows] == ["role"]


async def test_without_a_view_the_rows_carry_none(session):
    """`None` means the deployment default -- the same convention `Template.view_type`
    uses.

    Nullable and without a server default, for the reason that column states: a NOT
    NULL column cannot be added to a table that holds rows, and a default would
    silently pin every cached field to one named view.
    """
    provider = ViewAwareFakeProvider({None: FULL_VIEW})
    await refresh_catalogue(session, provider.for_view(None))

    rows = (await session.execute(select(DataField))).scalars().all()
    assert {row.view_type for row in rows} == {None}
