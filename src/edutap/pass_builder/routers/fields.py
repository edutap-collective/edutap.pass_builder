"""Data-provider field catalogue endpoints. Scope `manage`."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin.permissions import declares
from ..auth import AuthContext, require
from ..database import get_session
from ..dependencies import DataProviderFactory, get_data_provider_factory
from ..models.api import CatalogueExport, FieldResponse
from ..models.db import DataField
from ..models.enums import Scope
from ..services.retention import refresh_catalogue, views_in_use

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


@router.get(
    "/fields",
    response_model=list[FieldResponse],
    dependencies=[Depends(declares("fields:read"))],
)
async def list_fields(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[FieldResponse]:
    """Return the cached data_provider field catalogue."""
    rows = (await session.execute(select(DataField))).scalars().all()
    return [_to_response(row) for row in rows]


@router.post(
    "/fields/refresh",
    response_model=list[FieldResponse],
    dependencies=[Depends(declares("fields:refresh"))],
)
async def refresh_fields(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    build_provider: DataProviderFactory = Depends(get_data_provider_factory),  # noqa: B008
) -> list[FieldResponse]:
    """Replace the cached catalogue of EVERY view in use, and return all of it.

    EVERY VIEW, NOT THE DEPLOYMENT DEFAULT. `Template.view_type` gives each template
    its own view, and a rule is validated against the catalogue of ITS view. A refresh
    that covered only the default left every other view with an empty catalogue, and
    every rule against them failed as `unknown field` -- for fields the provider does
    offer.

    ONE CLIENT PER VIEW, from an injected FACTORY. `get_data_provider` hands out the
    client for the deployment default, because that is what a render needs; this
    endpoint is the one caller that needs several, and the provider serves one view per
    call. The factory stays a dependency so a test can still replace the provider --
    building the client in the route would have reached into `request.app.state` and
    made this endpoint untestable without a running lifespan.
    """
    for view_type in await views_in_use(session):
        await refresh_catalogue(session, build_provider(view_type), view_type=view_type)
    rows = (await session.execute(select(DataField))).scalars().all()
    return [_to_response(row) for row in rows]


@router.get(
    "/fields/catalogue.json",
    response_model=CatalogueExport,
    dependencies=[Depends(declares("fields:read"))],
)
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
