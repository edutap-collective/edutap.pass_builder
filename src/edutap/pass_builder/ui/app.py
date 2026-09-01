"""The management UI application factory.

A second ASGI application out of the same image, over the same database and
the same `services/` layer -- see
`docs/superpowers/specs/2026-09-01-management-ui-design.md` for why it lives
here rather than in a service of its own.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI

from ..app import exports_to_a_collector, observability  # noqa: F401  (shared install)
from ..errors import install_error_handlers
from ..routers import health
from ..settings import get_settings
from .routers import tenants

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
    app.include_router(api)

    # Outside UI_PREFIX, like the render application's: liveness and readiness
    # must be reachable without knowing the mount point.
    app.include_router(health.router)
    return app
