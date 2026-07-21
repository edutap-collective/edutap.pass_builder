"""FastAPI application factory and lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .dependencies import get_objectstore
from .errors import install_error_handlers
from .routers import audit, credentials, fields, health, passes, templates
from .settings import get_settings


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
    app = FastAPI(
        title="eduTAP pass builder",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    for router in (
        health.router,
        passes.router,
        templates.router,
        credentials.router,
        fields.router,
        audit.router,
    ):
        app.include_router(router)
    return app
