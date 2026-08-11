"""Shared fixtures for router (API) tests.

Router tests exercise the FastAPI app through an `httpx.AsyncClient` bound
to the app via `ASGITransport`, wired to the real Postgres-backed test
`session` (see the top-level `conftest.py`) via
`app.dependency_overrides[get_session]`, but with the object store and the
data_provider HTTP client replaced by in-memory fakes -- consistent with the
design spec's test layering (section 7): no real network, no real RustFS,
in unit-level API tests.

An async client is used rather than Starlette's synchronous `TestClient`
deliberately: `TestClient` runs the ASGI app on a second event loop in a
background thread, and asyncpg connections (including the ones backing the
shared test `session`) are bound to the event loop that created them --
crossing loops raises `RuntimeError` from asyncpg. Driving the app from the
same event loop the test and its `session` fixture already run on avoids
that entirely, and is also the modern, non-deprecated approach (Starlette
itself now warns that `httpx`+`TestClient` is deprecated).

`app.py`'s `lifespan` (which provisions a real object-store bucket) is
never triggered here -- every dependency an endpoint could reach for is
overridden explicitly instead.
"""

import base64
import io
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.app import create_app
from edutap.pass_builder.auth import hash_token
from edutap.pass_builder.clients.data_provider import CatalogueField
from edutap.pass_builder.database import get_session
from edutap.pass_builder.dependencies import get_data_provider, get_objectstore
from edutap.pass_builder.models.db import ApiClient, Tenant
from edutap.pass_builder.models.enums import Scope

# Settings() requires these; get_settings() is only ever exercised through
# these router tests (no other test module resolves it), so setting them
# once at collection time is safe and does not leak into other suites.
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_DATABASE_URL", "postgresql+asyncpg://unused/unused"
)
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY",
    base64.b64encode(os.urandom(32)).decode(),
)
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://data-provider.invalid"
)


@pytest.fixture(autouse=True)
async def schema(session):
    """Create every table once per test, in the test's own transaction."""
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


class FakeObjectStore:
    """In-memory object store, same shape as `tests/services/test_templates.py`."""

    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.storage[key] = data

    async def get(self, key: str) -> bytes:
        return self.storage[key]

    async def ping(self) -> None:
        """Always succeed -- there is no real bucket to be unreachable."""


class FakeDataProvider:
    """Records requested fields; returns configured field/catalogue data."""

    def __init__(self) -> None:
        self.response: dict[str, Any] = {}
        self.catalogue: list[CatalogueField] = []
        self.last_fields: list[str] | None = None
        self.fail: bool = False

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict[str, Any]:
        if self.fail:
            raise ConnectionError("data provider unreachable")
        self.last_fields = fields
        return self.response

    async def fetch_catalogue(self) -> list[CatalogueField]:
        if self.fail:
            raise ConnectionError("data provider unreachable")
        return self.catalogue


@pytest.fixture
def objectstore() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def data_provider() -> FakeDataProvider:
    return FakeDataProvider()


@pytest.fixture
def app(session, objectstore, data_provider):
    """A `create_app()` instance with I/O dependencies replaced by fakes."""

    async def override_get_session():
        yield session

    application = create_app()
    application.dependency_overrides[get_session] = override_get_session
    application.dependency_overrides[get_objectstore] = lambda: objectstore
    application.dependency_overrides[get_data_provider] = lambda: data_provider
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@dataclass
class SeededClient:
    """A tenant plus a bearer token for one of its API clients."""

    tenant: Tenant = field(repr=False)
    token: str

    @property
    def tenant_id(self):
        return self.tenant.id

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def seed_client(
    session, scopes: list[Scope], *, tenant_key: str | None = None
) -> SeededClient:
    """Create a tenant and one active API client with the given scopes."""
    tenant = Tenant(key=tenant_key or f"tenant-{uuid4().hex[:8]}", name="Tenant")
    session.add(tenant)
    await session.flush()
    token = f"token-{uuid4().hex}"  # noqa: S105 - a test fixture token, not a secret
    session.add(
        ApiClient(
            tenant_id=tenant.id,
            name="test-client",
            token_hash=hash_token(token),
            scopes=[s.value for s in scopes],
            active=True,
        )
    )
    await session.flush()
    return SeededClient(tenant=tenant, token=token)


def make_apple_bundle(
    *,
    primary_key: str = "name",
    icon: bytes = b"\x89PNG",
    include_icon: bool = True,
) -> bytes:
    """Build an in-memory `.pkpasstemplate`: pass.json (+ icon.png).

    `include_icon=False` yields a bundle that fails publish validation
    (`missing required asset: icon.png`) without needing mapping rules to
    be wrong -- used to exercise `/versions/{id}/validate`.
    """
    buffer = io.BytesIO()
    pass_json = (
        '{"formatVersion":1,"description":"Student ID",'
        '"organizationName":"Test Org","passTypeIdentifier":"pass.test.example",'
        '"teamIdentifier":"TEAMID123","generic":{"primaryFields":'
        f'[{{"key":"{primary_key}","label":"Name","value":""}}]}}}}'
    )
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pass.json", pass_json)
        if include_icon:
            zf.writestr("icon.png", icon)
    return buffer.getvalue()
