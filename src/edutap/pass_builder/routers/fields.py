"""Data-provider field catalogue endpoints. Scope `manage`."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require
from ..clients.data_provider import DataProviderClient
from ..database import get_session
from ..dependencies import get_data_provider
from ..models.api import CatalogueExport, FieldResponse
from ..models.db import DataField
from ..models.enums import Scope
from ..services.retention import refresh_catalogue

router = APIRouter(tags=["fields"])


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
    """Replace the cached catalogue from `data_provider` and return it."""
    await refresh_catalogue(session, data_provider)
    rows = (await session.execute(select(DataField))).scalars().all()
    return [_to_response(row) for row in rows]


@router.get("/fields/catalogue.json", response_model=CatalogueExport)
async def export_catalogue(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CatalogueExport:
    """Return the catalogue in the shape `edutap.pass_designer` loads.

    THE SAME CATALOGUE, NOT A SECOND ONE. The designer lays a pass out against
    a field list and this service validates every mapping rule against one; if
    those are two files, a rule authored in the designer fails at publish time
    and the difference is invisible until then. The designer ships a neutral
    example for development, and a deployment points it at this.

    Deliberately the *cached* rows rather than a live call to the data
    provider: what a rule is validated against is this cache, so what the
    designer draws against has to be the same thing. `POST /fields/refresh` is
    what moves both.
    """
    rows = (await session.execute(select(DataField))).scalars().all()
    return CatalogueExport(fields=[_to_response(row) for row in rows])
