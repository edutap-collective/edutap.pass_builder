"""Audit retention and field-catalogue refresh."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.data_provider import CatalogueField
from ..models.db import AuditLog, DataField, Template


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


async def views_in_use(session: AsyncSession) -> list[str | None]:
    """Return every view a refresh has to cover.

    `None` first, always: it is how the cache marks the deployment default, and a
    template with `view_type IS NULL` reads exactly that. It is also the most common
    case -- refreshing only the NAMED views would leave those templates with an empty
    catalogue, and every one of their rules would fail as `unknown field`.

    Then every distinct view a template names. A deployment whose default is
    `full_view` and a template that spells `full_view` out therefore yield two
    entries, `None` and `"full_view"`, holding the same fields at the cost of one
    extra fetch. Collapsing them would mean deciding here which spelling a template
    meant, and that decision belongs where the template is read.
    """
    named = (
        (
            await session.execute(
                select(Template.view_type)  # ty: ignore[no-matching-overload]
                .where(Template.view_type.is_not(None))  # ty: ignore[unresolved-attribute]
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return [None, *sorted(set(named))]


async def refresh_catalogue(
    session: AsyncSession,
    data_provider: SupportsFetchCatalogue,
    *,
    view_type: str | None = None,
) -> int:
    """Replace the cached `data_field` catalogue of ONE view. Return how many.

    THE VIEW, NOT THE CACHE. Every row OF THIS VIEW is deleted and replaced by the
    freshly fetched set rather than upserted by key, so a field the provider stops
    offering disappears too -- but the other views are left alone. Deleting the whole
    table would make refreshing one view silently empty the others, and the next
    mapping rule against them would fail with `unknown field` for a field nobody
    touched.

    `view_type` must name the view `data_provider` was built for. The client serves
    one view per call and the cache records which; passing one and fetching another
    would store the right fields under the wrong name, and nothing would notice until
    a rule was validated.

    `None` means the deployment default, the same convention `Template.view_type`
    uses.
    """
    catalogue = await data_provider.fetch_catalogue()
    # `is_(None)` rather than `== None`: SQL equality against NULL is never true, so
    # `view_type == None` would delete nothing and the refresh would accumulate
    # duplicates on every run.
    await session.execute(
        delete(DataField).where(
            DataField.view_type.is_(None)  # ty: ignore[unresolved-attribute]
            if view_type is None
            else DataField.view_type == view_type  # ty: ignore[invalid-argument-type]
        )
    )
    fetched_at = datetime.now(UTC)
    for entry in catalogue:
        session.add(
            DataField(
                key=entry.key,
                view_type=view_type,
                value_type=entry.value_type,
                kinds=list(entry.kinds),
                label=entry.label or entry.key,
                required=entry.required,
                description=entry.description,
                fetched_at=fetched_at,
            )
        )
    await session.flush()
    return len(catalogue)
