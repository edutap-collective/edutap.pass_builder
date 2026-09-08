"""Fixtures for the admin application.

Same shape as the router tests: the app over the Postgres-backed test
session, driven through ASGITransport. The admin sign-in is configured
through the environment (trusted_header), because that is the seam a
deployment uses.
"""

import base64
import os

import pytest
from httpx import ASGITransport, AsyncClient
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.database import get_session
from edutap.pass_builder.models.db import Tenant

# Settings() requires these -- same collection-time seeding as the router
# tests, and for the same reason.
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY",
    base64.b64encode(os.urandom(32)).decode(),
)
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://data-provider.invalid"
)
os.environ.setdefault("EDUTAP_PASS_BUILDER_DATA_PROVIDER_VIEW_TYPE", "full_view")
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_TENANTS", '[{"key": "lmu", "name": "LMU München"}]'
)

UB_KEY = "lmu-ub"
STWM_KEY = "stwm"


@pytest.fixture(autouse=True)
async def schema(session):
    """Create every table once per test, in the test's own transaction."""
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


@pytest.fixture
async def tenants(session):
    ub = Tenant(key=UB_KEY, name="University Library")
    stwm = Tenant(key=STWM_KEY, name="Student Union")
    session.add(ub)
    session.add(stwm)
    await session.flush()
    return {UB_KEY: ub, STWM_KEY: stwm}


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("EDUTAP_ADMIN_AUTH_BACKEND", "trusted_header")
    monkeypatch.setenv(
        "EDUTAP_ADMIN_AUTH_PERMISSIONS",
        '{"ub-admins": ["templates:read@lmu-ub", "templates:write@lmu-ub"],'
        ' "ub-readers": ["templates:read@lmu-ub"],'
        ' "auditors": ["audit:read@lmu-ub"]}',
    )


@pytest.fixture
def app(admin_env, session):
    from edutap.pass_builder.admin.app import create_admin_app

    async def override_get_session():
        yield session

    application = create_admin_app()
    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def as_person(*groups: str) -> dict[str, str]:
    return {"x-remote-user": "jdoe@lmu.de", "x-remote-groups": ";".join(groups)}
