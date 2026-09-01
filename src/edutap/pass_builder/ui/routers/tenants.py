"""Tenants and their API clients -- the two rows nothing else can create.

Every route of the render API resolves a bearer token against `api_client`,
and no route there creates a tenant or a client. That is not an oversight: a
machine credential should not be able to mint another. The UI can, because a
person is in front of it.
"""

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import hash_token
from ...database import get_session
from ...errors import ProblemError
from ...models.db import ApiClient, Tenant
from ...models.enums import Scope
from ..auth import Principal, require_principal

router = APIRouter(tags=["tenants"])

TOKEN_BYTES = 32
"""Entropy of a generated API token, before URL-safe encoding.

256 bits. The token is the only thing standing in front of a service that
signs passes, and it is never rotated on a schedule -- only when someone
decides to.
"""


class TenantIn(BaseModel):
    """A tenant to create."""

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class TenantOut(BaseModel):
    """A tenant as the UI shows it."""

    id: UUID
    key: str
    name: str


class ApiClientIn(BaseModel):
    """An API client to create."""

    name: str = Field(min_length=1, max_length=255)
    scopes: list[Scope] = Field(min_length=1)


class ApiClientOut(BaseModel):
    """An API client as the UI shows it -- never its token."""

    id: UUID
    name: str
    scopes: list[str]
    active: bool


class ApiClientCreated(ApiClientOut):
    """An API client the moment it is created, with its token.

    THE ONLY TIME THE TOKEN EXISTS IN READABLE FORM. Only its SHA-256 is
    stored, so this response is not repeatable: a token that is lost is
    replaced, not recovered. That is a property worth keeping -- a store that
    can show a token again is a store that can leak every token at once.
    """

    token: str


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    principal: Principal = Depends(require_principal()),  # noqa: B008
) -> list[Tenant]:
    """List every tenant.

    Not filtered by principal: authorisation for this UI is the allow-list in
    the settings, and someone who may use it at all may see the tenants. A
    per-tenant permission model would be a second authorisation system with
    one user in it.
    """
    rows = (await session.execute(select(Tenant).order_by(Tenant.key))).scalars().all()
    return list(rows)


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(
    body: TenantIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    principal: Principal = Depends(require_principal()),  # noqa: B008
) -> Tenant:
    """Create a tenant.

    A duplicate key is a 409 rather than an integrity error reaching the
    client: `Tenant.key` is unique, and re-creating one is a plausible mistake
    rather than a broken request.
    """
    tenant = Tenant(key=body.key, name=body.name)
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ProblemError(
            409, "tenant_exists", f"A tenant with key {body.key!r} already exists"
        ) from exc
    return tenant


@router.get("/tenants/{tenant_id}/clients", response_model=list[ApiClientOut])
async def list_clients(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    principal: Principal = Depends(require_principal()),  # noqa: B008
) -> list[ApiClient]:
    """List a tenant's API clients, without their tokens."""
    await _require_tenant(session, tenant_id)
    rows = (
        (
            await session.execute(
                select(ApiClient)
                .where(ApiClient.tenant_id == tenant_id)  # ty: ignore[invalid-argument-type]
                .order_by(ApiClient.name)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post(
    "/tenants/{tenant_id}/clients", response_model=ApiClientCreated, status_code=201
)
async def create_client(
    tenant_id: UUID,
    body: ApiClientIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    principal: Principal = Depends(require_principal()),  # noqa: B008
) -> ApiClientCreated:
    """Create an API client and return its token, once.

    One client per calling service rather than one shared between them: a
    compromised service is then revoked on its own, and `audit_log` says which
    service rendered. A shared token names the same caller four times.
    """
    await _require_tenant(session, tenant_id)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    client = ApiClient(
        tenant_id=tenant_id,
        name=body.name,
        token_hash=hash_token(token),
        scopes=[scope.value for scope in body.scopes],
    )
    session.add(client)
    await session.flush()
    return ApiClientCreated(
        id=client.id,
        name=client.name,
        scopes=client.scopes,
        active=client.active,
        token=token,
    )


@router.post("/tenants/{tenant_id}/clients/{client_id}/revoke", status_code=204)
async def revoke_client(
    tenant_id: UUID,
    client_id: UUID,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    principal: Principal = Depends(require_principal()),  # noqa: B008
) -> None:
    """Deactivate an API client.

    Deactivated rather than deleted: `audit_log` rows reference the client
    that made each request, and a deleted row turns a year of history into
    unattributable entries.
    """
    client = (
        await session.execute(
            select(ApiClient).where(
                ApiClient.id == client_id,  # ty: ignore[invalid-argument-type]
                ApiClient.tenant_id == tenant_id,  # ty: ignore[invalid-argument-type]
            )
        )
    ).scalar_one_or_none()
    if client is None:
        raise ProblemError(404, "client_not_found", "API client not found")
    client.active = False
    session.add(client)
    await session.flush()


async def _require_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Return the tenant, or refuse with a 404."""
    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)  # ty: ignore[invalid-argument-type]
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise ProblemError(404, "tenant_not_found", "Tenant not found")
    return tenant
