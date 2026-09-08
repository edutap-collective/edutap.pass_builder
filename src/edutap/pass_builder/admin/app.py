"""The admin application factory: the management routers, a third time.

ADR 0001 -- administration moves out of this service. The routers themselves
are unchanged; what this application adds is WHO is calling (a person, from
`edutap.admin_auth`'s configured sign-in) and WHAT they may do (the
permission each route declares, checked against the settings-driven map).

Zones: this application is published on the back-office entry point as a
second Traefik router. `passes` is deliberately not mounted -- rendering a
person's pass is not a management action, and leaving it out keeps this
application free of the one route whose zone matters.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from edutap.admin_auth import AdminAuth, AdminAuthSettings
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..app import exports_to_a_collector, observability  # noqa: F401  (shared install)
from ..auth import AuthContext, current_auth
from ..database import get_session
from ..errors import install_error_handlers
from ..models.db import Tenant
from ..models.enums import Scope
from ..routers import audit, credentials, fields, health, templates
from ..settings import get_settings

ADMIN_PREFIX = "/builder/v1/admin"
"""Inside the render API's namespace, one segment deeper.

The admin-ui design record publishes it as
``/internal-api/wallet/builder/v1/admin/...`` -- the same base path the
consumer API carries, so a generated client's server URL differs only in the
entry point it must be reached through. The zone boundary is Traefik's, not
the path's.
"""

TENANT_PREFIX = "/tenants/{tenant_id}"


class TenantOut(BaseModel):
    """A tenant as the admin interface shows it."""

    id: UUID
    key: str
    name: str
    active: bool


async def _tenant_key(request: Request, tenant_id: str) -> str:
    """Resolve the path's tenant UUID to the key permissions carry.

    Through the same `get_session` factory the routes use, honouring a
    test's dependency override -- there the override hands back the
    request's session, so the lookup sees rows seeded in the test's
    transaction. In production, where nothing overrides it, this is a
    second short-lived session on the same engine: it reads committed
    tenant rows, which is all this lookup needs.
    """
    try:
        wanted = UUID(tenant_id)
    except ValueError as error:
        raise HTTPException(404, "Tenant not found") from error
    factory = request.app.dependency_overrides.get(get_session, get_session)
    generator = factory()
    try:
        session = await anext(generator)
        key = (
            await session.execute(
                select(Tenant.key).where(Tenant.id == wanted)  # ty: ignore[no-matching-overload]
            )
        ).scalar_one_or_none()
    finally:
        await generator.aclose()
    if key is None:
        raise HTTPException(404, "Tenant not found")
    return key


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Provision the shared HTTP client the reused routers expect.

    No tenant reconciliation here: the render and UI applications already do
    it at startup, and this application treats tenants as read-only.
    """
    async with httpx.AsyncClient() as http:
        app.state.http = http
        yield


def create_admin_app() -> FastAPI:
    """Build the admin application."""
    settings = get_settings()
    auth = AdminAuth.from_settings(AdminAuthSettings(), tenant_resolver=_tenant_key)
    app = FastAPI(
        title="eduTAP pass builder — admin API",
        version="0.1.0",
        root_path=settings.base_path,
        docs_url=f"{ADMIN_PREFIX}/docs",
        openapi_url=f"{ADMIN_PREFIX}/openapi.json",
        redoc_url=f"{ADMIN_PREFIX}/redoc",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=ADMIN_PREFIX)

    @api.get("/tenants", response_model=list[TenantOut])
    async def list_tenants(
        request: Request,
        session: AsyncSession = Depends(get_session),  # noqa: B008
    ) -> list[Tenant]:
        """List every tenant, behind authentication alone.

        There is no ``tenants:read`` in the vocabulary and no tenant segment
        in this path to check against; the admin-ui design record asks the
        interface to "show tenants and never create them", and whoever is in
        the permission map at all needs this list to navigate.
        """
        await auth.current_identity(request)
        rows = (
            (await session.execute(select(Tenant).order_by(Tenant.key))).scalars().all()
        )
        return list(rows)

    async def tenant_path_parameter(tenant_id: str) -> str:
        """Declare `{tenant_id}` so it exists in the OpenAPI document.

        Same reasoning as the management UI's copy: FastAPI documents the
        parameters an operation declares, and none of the reused routes
        declares this one. `str` and not `UUID`, so the not-a-uuid answer
        stays the resolver's 404 rather than depending on dependency order.
        """
        return tenant_id

    managed = APIRouter(
        prefix=TENANT_PREFIX,
        dependencies=[Depends(tenant_path_parameter)],
    )
    for router in (
        templates.router,
        credentials.router,
        fields.router,
        audit.router,
    ):
        managed.include_router(router)
    api.include_router(managed)
    app.include_router(api)

    # The seam, third use. The reused routers ask `current_auth` who is
    # calling; here it is the person the configured sign-in established. Every
    # scope is granted -- scopes limit machine credentials, and the actual
    # control is the permission each route declares, checked below.
    async def admin_auth_context(request: Request) -> AuthContext:
        identity = await auth.current_identity(request)
        tenant_id = request.path_params.get("tenant_id")
        if tenant_id is None:
            raise HTTPException(404, "Not a tenant-scoped route")
        try:
            wanted = UUID(str(tenant_id))
        except ValueError as error:
            raise HTTPException(404, "Tenant not found") from error
        return AuthContext(
            principal=identity.subject, tenant_id=wanted, scopes=set(Scope)
        )

    app.dependency_overrides[current_auth] = admin_auth_context

    # What turns each route's declared permission into an enforced one: the
    # checker `declares()` looks for. Absent in every other mount.
    app.state.admin_permission_check = auth.check

    # Outside ADMIN_PREFIX, like the other applications': liveness and
    # readiness must be reachable without knowing the mount point.
    app.include_router(health.router)
    return app
