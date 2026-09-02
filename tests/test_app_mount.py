"""The service's mount point and the shape of its route table.

No database, no client: these assertions are about paths only. The three
required settings are set here because create_app() resolves get_settings().
"""

import pytest
from httpx import ASGITransport, AsyncClient

from edutap.pass_builder.app import API_PREFIX, create_app
from edutap.pass_builder.settings import get_settings


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    """Required settings plus a clean get_settings cache for every test."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def route_paths(app) -> set[str]:
    """Every *schema-visible* path the app routes, without the mount point.

    FastAPI >=0.141 stores included routers as lazy proxy objects on
    ``app.routes`` (its "avoid flattening" performance refactors, released
    2026-07-27..29) instead of eagerly flattening them into plain routes with
    a ``.path`` attribute, so ``hasattr(route, "path")`` no longer finds the
    business and health routes. The OpenAPI schema is the stable, public
    place to read the fully resolved, prefix-composed route table -- but it
    only contains routes with ``include_in_schema=True``. A route hidden from
    the schema would not show up here and would not be caught by
    ``test_no_route_carries_the_old_hardcoded_prefix``.
    """
    return set(app.openapi()["paths"])


def test_api_prefix_is_the_agreed_path():
    """Pin the literal value.

    Every other assertion in this file composes paths FROM API_PREFIX, so a
    wrong constant would keep them all green. This is the one place that
    states what the contract actually is -- the path the webfe and the
    calling services expect.
    """
    assert API_PREFIX == "/builder/v1"


def test_root_path_comes_from_the_settings():
    assert create_app().root_path == "/internal-api/wallet"


def test_root_path_follows_a_zone_change(monkeypatch):
    """Moving zones is configuration, not a code change."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_CLASS", "api")
    get_settings.cache_clear()
    assert create_app().root_path == "/api/wallet"


def test_business_routes_carry_the_api_prefix():
    paths = route_paths(create_app())
    assert f"{API_PREFIX}/passes" in paths
    assert f"{API_PREFIX}/templates" in paths
    assert f"{API_PREFIX}/credentials" in paths
    assert f"{API_PREFIX}/fields" in paths
    assert f"{API_PREFIX}/audit" in paths


def test_health_routes_stay_outside_the_api_prefix():
    """The Docker healthcheck hits the container directly and must not need
    to know the mount point."""
    paths = route_paths(create_app())
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_no_route_carries_the_old_hardcoded_prefix():
    assert not [
        path for path in route_paths(create_app()) if path.startswith("/api/v1")
    ]


async def test_healthz_answers_both_bare_and_root_path_prefixed():
    """`/healthz` must be reachable two ways from the same running process.

    Starlette only strips `root_path` from the incoming path when the raw
    path already carries it (`starlette._utils.get_route_path`); otherwise it
    falls back to matching the raw path unchanged. That is exactly what lets
    one process answer both the Docker healthcheck, which hits the container
    directly at the bare path, and Traefik forwarding the full path without a
    stripprefix middleware -- the two deployments `app.py`'s `root_path`
    comment promises to support. `/healthz` has no dependencies (unlike
    `/readyz`, which needs the database, object store and data provider), so
    it needs no fixtures beyond the settings this module already sets.
    """
    app = create_app()
    prefixed_path = f"{get_settings().base_path}/healthz"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        bare_response = await client.get("/healthz")
        prefixed_response = await client.get(prefixed_path)

    assert bare_response.status_code == 200
    assert prefixed_response.status_code == 200


async def test_documentation_urls_are_served_under_the_api_prefix():
    """`docs_url`, `openapi_url` and `redoc_url` all moved under `API_PREFIX`
    in app.py; prove each is actually served there rather than only
    appearing in `route_paths()`, which reads the schema itself and so
    cannot check where the schema (or the two UIs built on top of it) are
    exposed. One test covering all three keeps a left-behind `redoc_url`
    (or any future documentation setting) from going unnoticed again."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        openapi_response = await client.get(f"{API_PREFIX}/openapi.json")
        docs_response = await client.get(f"{API_PREFIX}/docs")
        redoc_response = await client.get(f"{API_PREFIX}/redoc")

    assert openapi_response.status_code == 200
    assert docs_response.status_code == 200
    assert redoc_response.status_code == 200
