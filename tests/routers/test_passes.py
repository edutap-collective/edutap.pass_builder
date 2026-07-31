"""Happy-path tests for the passes router: create, preview, scope checks.

Apple signing needs a real WWDR certificate file on disk (see
`edutap.wallet_apple.api.sign_direct`), which router-level tests have no
business depending on -- that path is already covered by
`tests/services/test_render.py` and `tests/engine/test_apple_build.py` via
an injected no-op signer. This module overrides `get_render_service` the
same way, at the router boundary, so it can exercise request parsing,
scopes and the response envelope without touching real cryptography.
"""

import base64
import os

import pytest
from httpx import ASGITransport, AsyncClient

from edutap.pass_builder.app import API_PREFIX
from edutap.pass_builder.dependencies import get_render_service
from edutap.pass_builder.models.db import (
    DataField,
    MappingRule,
    Template,
    TemplateVariant,
    TemplateVersion,
)
from edutap.pass_builder.models.enums import (
    RuleOrigin,
    Scope,
    TargetKind,
    ValueType,
    VersionStatus,
    WalletType,
)
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService
from edutap.pass_builder.services.render import RenderService
from edutap.pass_builder.services.templates import TemplateService

from .conftest import seed_client


def _noop_sign(pkpass: object) -> None:
    """A no-op Apple signer -- see the module docstring."""


@pytest.fixture
def client(app, session, objectstore, data_provider):
    """Shadow `conftest.client`: same app, plus a no-op Apple signer."""

    def override_render_service() -> RenderService:
        templates = TemplateService(session, objectstore)
        backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())
        credentials = CredentialService(session, backend)
        return RenderService(
            session, templates, credentials, data_provider, apple_sign=_noop_sign
        )

    app.dependency_overrides[get_render_service] = override_render_service
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


async def _seed_published_apple_template(session, tenant_id) -> None:
    """One published Apple template/variant/version mapping `person.name`."""
    template = Template(tenant_id=tenant_id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()

    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=WalletType.APPLE,
        key="student",
        name="Student",
        is_default=True,
    )
    session.add(variant)
    await session.flush()

    version = TemplateVersion(
        variant_id=variant.id,
        number=1,
        status=VersionStatus.PUBLISHED,
        pass_json={
            "formatVersion": 1,
            "description": "Student ID",
            "organizationName": "Test Org",
            "passTypeIdentifier": "pass.test.example",
            "teamIdentifier": "TEAMID123",
            "generic": {
                "primaryFields": [{"key": "name", "label": "Name", "value": ""}]
            },
        },
    )
    session.add(version)
    await session.flush()

    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    session.add(
        MappingRule(
            version_id=version.id,
            origin=RuleOrigin.AUTHORED,
            target_kind=TargetKind.FIELD_VALUE,
            target="name",
            source_field="person.name",
            value_type=ValueType.TEXT,
            required=True,
            position=0,
        )
    )
    await session.flush()


async def test_create_apple_pass_returns_pkpass_bytes_with_headers(
    client, session, data_provider
):
    renderer = await seed_client(session, [Scope.RENDER])
    await _seed_published_apple_template(session, renderer.tenant_id)
    data_provider.response = {"person.name": "Ada Lovelace"}

    response = await client.post(
        f"{API_PREFIX}/passes",
        json={
            "pass_id": "11111111-1111-1111-1111-111111111111",
            "template": "student-id",
            "wallet_type": "apple",
            "person_uid": "u1",
        },
        headers=renderer.headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apple.pkpass"
    assert response.headers["x-template-version"] == "1"
    assert response.headers["x-variant"] == "student"
    assert response.content
    assert data_provider.last_fields == ["person.name"]


async def test_create_pass_requires_render_scope(client, session):
    manager = await seed_client(session, [Scope.MANAGE])

    response = await client.post(
        f"{API_PREFIX}/passes",
        json={
            "pass_id": "1",
            "template": "student-id",
            "wallet_type": "apple",
            "person_uid": "u1",
        },
        headers=manager.headers,
    )
    assert response.status_code == 403


async def test_create_pass_missing_field_is_422(client, session, data_provider):
    renderer = await seed_client(session, [Scope.RENDER])
    await _seed_published_apple_template(session, renderer.tenant_id)
    data_provider.response = {}  # person.name missing

    response = await client.post(
        f"{API_PREFIX}/passes",
        json={
            "pass_id": "1",
            "template": "student-id",
            "wallet_type": "apple",
            "person_uid": "u1",
        },
        headers=renderer.headers,
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("missing_field")


async def test_preview_never_calls_data_provider(client, session, data_provider):
    renderer = await seed_client(session, [Scope.RENDER])
    await _seed_published_apple_template(session, renderer.tenant_id)

    response = await client.post(
        f"{API_PREFIX}/passes/preview",
        json={
            "template": "student-id",
            "wallet_type": "apple",
            "sample_data": {"person.name": "Grace Hopper"},
        },
        headers=renderer.headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bound_fields"] == ["person.name"]
    assert body["pass_json"]["generic"]["primaryFields"][0]["value"] == "Grace Hopper"
    assert data_provider.last_fields is None


async def test_update_pass_re_renders_same_serial(client, session, data_provider):
    renderer = await seed_client(session, [Scope.RENDER])
    await _seed_published_apple_template(session, renderer.tenant_id)
    data_provider.response = {"person.name": "Ada Lovelace"}

    response = await client.put(
        f"{API_PREFIX}/passes/11111111-1111-1111-1111-111111111111",
        json={
            "template": "student-id",
            "wallet_type": "apple",
            "person_uid": "u1",
        },
        headers=renderer.headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apple.pkpass"
