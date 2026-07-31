"""FastAPI application factory and lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI

from .dependencies import get_objectstore
from .errors import install_error_handlers
from .routers import audit, credentials, fields, health, passes, templates
from .settings import get_settings

API_PREFIX = "/builder/v1"
"""This service's path inside its zone: /<service>/v<n>.

Deliberately in the code, not in the settings: this is the API contract, and
it belongs where it can be read and tested. Only the mount point in front of
it (settings.base_path) varies per deployment.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Provision the shared HTTP client and the object-store bucket.

    The `httpx.AsyncClient` is created once and reused across every request
    (`dependencies.get_data_provider` reads it back from `app.state.http`)
    rather than opened anew per call. The object-store bucket is created if
    missing so the first template import never fails only because
    provisioning was skipped.
    """
    async with httpx.AsyncClient() as http:
        app.state.http = http
        objectstore = get_objectstore(get_settings())
        await objectstore.ensure_bucket()
        yield


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="eduTAP pass builder",
        version="0.1.0",
        # root_path, not a router prefix: Starlette strips it when matching, so
        # the very same process answers both behind a proxy that does not strip
        # (Traefik, no stripprefix middleware) and on a direct container hit --
        # which is what the Docker healthcheck does. It also puts the mount
        # point into openapi.json's `servers`, so generated clients are correct.
        root_path=settings.base_path,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=f"{API_PREFIX}/redoc",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    for router in (
        passes.router,
        templates.router,
        credentials.router,
        fields.router,
        audit.router,
    ):
        api.include_router(router)
    app.include_router(api)

    # Outside API_PREFIX on purpose: liveness and readiness must be reachable
    # without knowing the mount point.
    app.include_router(health.router)
    return app
