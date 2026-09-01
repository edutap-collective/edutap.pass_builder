"""The management UI application factory.

A second ASGI application out of the same image, over the same database and
the same `services/` layer -- see
`docs/superpowers/specs/2026-09-01-management-ui-design.md` for why it lives
here rather than in a service of its own.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from ..app import exports_to_a_collector, observability  # noqa: F401  (shared install)
from ..auth import current_auth
from ..errors import install_error_handlers
from ..routers import audit, credentials, fields, health, templates
from ..settings import get_settings
from .auth import tenant_path_parameter, ui_auth_context
from .routers import tenants

TENANT_PREFIX = "/tenants/{tenant_id}"
"""Where the reused management routers hang.

A token names exactly one tenant, so the render API never has to say which; a
person does not, so the UI does. Everything below this prefix is still
tenant-scoped by that value -- what changes is where it comes from, not
whether it applies.
"""

STATIC_DIR = Path(__file__).parent / "static"
"""Where `pnpm build` writes the single-page application.

Absent in a source checkout that has not built the frontend, and that is not
an error: the API is the whole service as far as the tests are concerned, and
a developer working on the Python half should not need a Node toolchain to run
it. `make build-frontend` fills it; the Docker build does it in its own stage.
"""

UI_PREFIX = "/builder-ui/v1"
"""The UI's path inside its zone.

Its own segment rather than the render API's `/builder/v1`: the two are
different contracts with different callers, and a generated client for one
should not find the other's routes in its schema.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Provision the shared HTTP client.

    The same client the render application keeps, for the same reason: the
    catalogue export and the Google class push both talk HTTP, and a pool per
    request is a pool that never warms up.
    """
    async with httpx.AsyncClient() as http:
        app.state.http = http
        yield


def create_ui_app() -> FastAPI:
    """Build the management UI application."""
    settings = get_settings()
    app = FastAPI(
        title="eduTAP pass builder — management",
        version="0.1.0",
        root_path=settings.ui_base_path,
        docs_url=f"{UI_PREFIX}/docs",
        openapi_url=f"{UI_PREFIX}/openapi.json",
        redoc_url=f"{UI_PREFIX}/redoc",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=UI_PREFIX)
    api.include_router(tenants.router)

    # The management routers themselves, unchanged and unduplicated.
    #
    # `passes.router` is deliberately absent: rendering a person's pass is not
    # a management action, and the UI has no reason to be able to do it. It
    # also keeps this application free of the one route whose zone matters.
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

    # The seam. Those routers ask `current_auth` who is calling; here a person
    # answers, and the tenant comes from the path segment above.
    app.dependency_overrides[current_auth] = ui_auth_context

    # Outside UI_PREFIX, like the render application's: liveness and readiness
    # must be reachable without knowing the mount point.
    app.include_router(health.router)

    # The single-page application, mounted last so it can claim "/" without
    # shadowing anything: Starlette matches routes in the order they were
    # added, and a mount at the root added earlier would swallow every API
    # path above it.
    #
    # `html=True` serves `index.html` for a path the build produced no file
    # for, which is what a reload of a deep link needs.
    #
    # THE SHELL CARRIES NO PRINCIPAL CHECK, and that is right: it is a bundle
    # of JavaScript, not data. Everything it then asks for goes through the
    # same allow-list as any other call.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
