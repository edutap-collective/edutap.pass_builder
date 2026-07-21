"""Audit retention and field-catalogue refresh."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.data_provider import CatalogueField
from ..models.db import AuditLog, DataField
from ..models.enums import ValueType


class SupportsFetchCatalogue(Protocol):
    """The subset of `DataProviderClient` `refresh_catalogue` depends on.

    A `Protocol` rather than the concrete client so unit tests can inject a
    fake that never touches the network.
    """

    async def fetch_catalogue(self) -> list[CatalogueField]:
        """Return the field catalogue offered by the data provider."""
        ...


async def purge_expired_audit(
    session: AsyncSession, retention_months: int, now: datetime
) -> int:
    """Delete audit entries older than the retention window; return the count.

    `now` is injected by the caller rather than read here, keeping this
    function deterministic and easy to pin down in tests.
    """
    cutoff = now - timedelta(days=retention_months * 30)
    result = await session.execute(
        delete(AuditLog).where(
            AuditLog.ts < cutoff  # ty: ignore[invalid-argument-type]
        )
    )
    return result.rowcount or 0  # ty: ignore[unresolved-attribute]


async def refresh_catalogue(
    session: AsyncSession, data_provider: SupportsFetchCatalogue
) -> int:
    """Replace the cached `data_field` catalogue from `data_provider`.

    Every existing row is deleted and replaced by the freshly fetched set
    rather than upserted by key, so a field the data provider stops
    offering disappears from the cache too. Returns the number of fields
    loaded.
    """
    catalogue = await data_provider.fetch_catalogue()
    await session.execute(delete(DataField))
    fetched_at = datetime.now(UTC)
    for entry in catalogue:
        session.add(
            DataField(
                key=entry.key,
                value_type=ValueType(entry.value_type),
                label=entry.label or entry.key,
                required=entry.required,
                description=entry.description,
                fetched_at=fetched_at,
            )
        )
    await session.flush()
    return len(catalogue)
