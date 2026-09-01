"""The management UI wired to the Postgres-backed test session.

Same layering as `tests/routers/conftest.py`: a real database, no network, and
the principal asserted through headers exactly as the web frontend would.
"""

import base64
import os

import pytest
from httpx import ASGITransport, AsyncClient
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.database import get_session
from edutap.pass_builder.settings import get_settings
from edutap.pass_builder.ui.app import UI_PREFIX, create_ui_app

# Settings() requires these, and `get_settings` is cached process-wide.
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY",
    base64.b64encode(os.urandom(32)).decode(),
)
os.environ.setdefault(
    "EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://data-provider.invalid"
)

AUTHORISED = "alexander@example.org"
AUTHORISED_GROUP = "wallet-admins"


@pytest.fixture(autouse=True)
async def _schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


@pytest.fixture(autouse=True)
def _allow_list(monkeypatch):
    """One named principal and one group, the shape a first deployment has."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_UI_AUTHORISED_USERS", AUTHORISED)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_UI_AUTHORISED_GROUPS", AUTHORISED_GROUP)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ui_app(session):
    async def override_get_session():
        yield session

    application = create_ui_app()
    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture
async def ui(ui_app):
    """A client that presents the allow-listed principal on every request."""
    transport = ASGITransport(app=ui_app)
    async with AsyncClient(
        transport=transport,
        base_url=f"http://ui{UI_PREFIX}",
        headers={"REMOTE_USER": AUTHORISED},
    ) as client:
        yield client


@pytest.fixture
async def anonymous_ui(ui_app):
    """A client that asserts no principal at all."""
    transport = ASGITransport(app=ui_app)
    async with AsyncClient(
        transport=transport, base_url=f"http://ui{UI_PREFIX}"
    ) as client:
        yield client
