"""FastAPI application factory and lifespan."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

import httpx
from edutap.observability_settings import (
    OTLP_ENDPOINT_VARIABLE,
    ObservabilitySettings,
    install_observability,
    instrument_fastapi_safely,
)
from fastapi import APIRouter, FastAPI

from .dependencies import get_objectstore
from .errors import install_error_handlers
from .routers import audit, credentials, fields, health, passes, templates
from .settings import get_settings

SERVICE_NAME = "edutap.pass_builder"
"""The name telemetry travels under: the distribution name.

The same spelling as `pip show` prints and as the sibling services use. In Loki
`service_name` is the only indexed label, so one spelling across a span, a log
line and the installed distribution is what makes it selectable at all.
"""

#: Error reporting, tracing and structured logging, resolved at import.
#:
#: At import and not in `create_app`, deliberately: `install_observability` exists
#: to be called *before* a service resolves the settings it needs to run, so that a
#: process refusing to start is reported rather than silently absent. `create_app`
#: reads `Settings`, which can raise on a malformed value or a missing master key.
#:
#: Reading this can never fail for want of a value: no field of
#: `ObservabilitySettings` is required, which is what makes the ordering possible.
#:
#: The prefix is `EDUTAP_`, not this package's own -- these fields are defined by an
#: eduTAP package and mean the same thing in every eduTAP service.
observability = ObservabilitySettings()
install_observability(
    observability,
    service_name=SERVICE_NAME,
    service_version=version(SERVICE_NAME),
)


def exports_to_a_collector() -> bool:
    """Whether an exporter will actually carry a span off this process.

    Both conditions are needed: `telemetry_enabled` is the deliberate off
    switch, and the endpoint decides whether anything is listening. The
    endpoint is read from the environment rather than from a field because
    `OTEL_EXPORTER_OTLP_ENDPOINT` is the variable every OpenTelemetry SDK reads
    by itself -- giving it a second name here would ask an operator to set the
    same address twice.
    """
    return observability.telemetry_enabled and bool(
        os.environ.get(OTLP_ENDPOINT_VARIABLE)
    )


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
    # Instrumenting needs the finished route table, which exists by the time a
    # lifespan runs but not while `create_app` is still building the object.
    #
    # ONLY WHEN SOMETHING EXPORTS. Instrumentation patches the application whether
    # or not a receiver exists, and a span nobody collects is work done on every
    # request.
    if exports_to_a_collector():
        instrument_fastapi_safely(app, observability)

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
