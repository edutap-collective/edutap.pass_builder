"""Data-provider field catalogue endpoints. Scope `manage`."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require
from ..clients.data_provider import DataProviderClient
from ..database import get_session
from ..dependencies import get_data_provider
from ..models.api import FieldResponse
from ..models.db import DataField
from ..models.enums import Scope, ValueType

router = APIRouter(prefix="/api/v1", tags=["fields"])


def _to_response(field: DataField) -> FieldResponse:
    """Map a cached `DataField` row onto its response schema."""
    return FieldResponse(
        key=field.key,
        value_type=field.value_type,
        label=field.label,
        required=field.required,
        description=field.description,
    )


@router.get("/fields", response_model=list[FieldResponse])
async def list_fields(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[FieldResponse]:
    """Return the cached data_provider field catalogue."""
    rows = (await session.execute(select(DataField))).scalars().all()
    return [_to_response(row) for row in rows]


@router.post("/fields/refresh", response_model=list[FieldResponse])
async def refresh_fields(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    data_provider: DataProviderClient = Depends(get_data_provider),  # noqa: B008
) -> list[FieldResponse]:
    """Refresh the cached catalogue from `data_provider`, upserting by key."""
    catalogue = await data_provider.fetch_catalogue()
    existing = {
        row.key: row
        for row in (await session.execute(select(DataField))).scalars().all()
    }
    now = datetime.now(UTC)
    refreshed: list[DataField] = []
    for entry in catalogue:
        row = existing.get(entry.key)
        value_type = ValueType(entry.value_type)
        if row is None:
            row = DataField(
                key=entry.key,
                value_type=value_type,
                label=entry.label or entry.key,
                required=entry.required,
                description=entry.description,
                fetched_at=now,
            )
        else:
            row.value_type = value_type
            row.label = entry.label or entry.key
            row.required = entry.required
            row.description = entry.description
            row.fetched_at = now
        session.add(row)
        refreshed.append(row)
    await session.flush()
    return [_to_response(row) for row in refreshed]
