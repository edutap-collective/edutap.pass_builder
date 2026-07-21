"""FastAPI application factory."""

from fastapi import FastAPI

from .errors import install_error_handlers
from .routers import health


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="eduTAP pass builder",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    install_error_handlers(app)
    app.include_router(health.router)
    return app
