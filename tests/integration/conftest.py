"""Fixtures for the full-stack integration test (real DB, RustFS, Apple signing).

`e2e_env` wires the real services together -- `CredentialService`,
`TemplateService` and `RenderService` -- against the same Postgres
testcontainer the rest of the suite uses (see the top-level `conftest.py`'s
`session` fixture) and a real `ObjectStore` bound to the compose `objectstore`
(RustFS) service. Only the data provider is faked, via `httpx.MockTransport`
rather than a real network call, since `edutap.data_provider` is a separate
service this repository does not run.
"""

import base64
import io
import json
import os
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.clients.data_provider import DataProviderClient
from edutap.pass_builder.clients.objectstore import ObjectStore
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.db import ApiClient, DataField, Tenant
from edutap.pass_builder.models.enums import Scope, TargetKind, ValueType, WalletType
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService
from edutap.pass_builder.services.render import RenderResult, RenderService
from edutap.pass_builder.services.templates import TemplateService
from edutap.pass_builder.settings import Settings

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_PASS_TYPE_IDENTIFIER = "pass.test.local"  # noqa: S105 - an identifier, not a secret
_TEAM_IDENTIFIER = "TEST123456"
_TEMPLATE_KEY = "e2e-student-id"
_SAMPLE_PERSON_DATA = {"person.name": "Ada Lovelace"}


def _make_apple_bundle() -> bytes:
    """Build an in-memory `.pkpasstemplate`: a minimal valid pass.json + icon.png."""
    pass_json = {
        "formatVersion": 1,
        "description": "eduTAP end-to-end test pass",
        "organizationName": "eduTAP Test",
        "passTypeIdentifier": _PASS_TYPE_IDENTIFIER,
        "teamIdentifier": _TEAM_IDENTIFIER,
        "generic": {"primaryFields": [{"key": "name", "label": "Name", "value": ""}]},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pass.json", json.dumps(pass_json))
        archive.writestr("icon.png", b"\x89PNG\r\n\x1a\n")
    return buffer.getvalue()


def _fake_data_provider_transport() -> httpx.MockTransport:
    """Return a transport that answers `/lookup` with a fixed sample person.

    Stands in for a running `edutap.data_provider` instance -- no real
    network call is made, per the project's HTTPX testing convention.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/lookup")
        return httpx.Response(200, json=_SAMPLE_PERSON_DATA)

    return httpx.MockTransport(handler)


@dataclass
class E2eEnv:
    """Everything the end-to-end test needs to render one real Apple pass."""

    settings: Settings
    service: RenderService
    auth: AuthContext

    async def create_apple_pass(self, *, person_uid: str) -> RenderResult:
        """Render a fresh Apple pass for `person_uid` through the real stack."""
        return await self.service.create_pass(
            self.auth,
            pass_id=str(uuid4()),
            template_key=_TEMPLATE_KEY,
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid=person_uid,
            version_number=None,
        )


@pytest.fixture
async def e2e_env(session, monkeypatch) -> AsyncIterator[E2eEnv]:
    """Seed a tenant, an Apple credential, a published template and a data provider.

    Runs against the Postgres testcontainer (`session`, from the top-level
    `conftest.py`) and a real `ObjectStore` bound to the compose
    `objectstore` (RustFS) service -- both must be reachable, hence this
    fixture only ever runs under `pytest.mark.integration`.
    """
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))

    wwdr_path = _FIXTURES_DIR / "wwdr-g4.pem"
    # `edutap.wallet_apple.api.sign_direct` reads the WWDR certificate from
    # its own process-wide `Settings`, not this project's -- see the task
    # report for why `settings.wwdr_certificate_path` below cannot drive it.
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_WWDR_CERTIFICATE", str(wwdr_path))

    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        secret_master_key=base64.b64encode(os.urandom(32)).decode(),
        data_provider_base_url="http://data-provider.invalid",
        wwdr_certificate_path=wwdr_path,
        objectstore_bucket="pass-builder",
        objectstore_endpoint_url="http://localhost:9000",
        objectstore_access_key="pass_builder",
        objectstore_secret_key="pass_builder",  # noqa: S106 - compose test credential
    )

    objectstore = ObjectStore(
        endpoint_url=settings.objectstore_endpoint_url,
        bucket=settings.objectstore_bucket,
        access_key=settings.objectstore_access_key,
        secret_key=settings.objectstore_secret_key.get_secret_value(),
    )
    await objectstore.ensure_bucket()

    tenant = Tenant(key=f"e2e-{uuid4().hex[:8]}", name="E2E Tenant")
    session.add(tenant)
    await session.flush()
    api_client = ApiClient(
        tenant_id=tenant.id,
        name="e2e-renderer",
        token_hash=f"unused-{uuid4().hex}",
        scopes=[Scope.RENDER],
    )
    session.add(api_client)
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()

    backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())
    credentials = CredentialService(session, backend)
    key_pem = (_FIXTURES_DIR / "test_signing_key.pem").read_bytes()
    cert_pem = (_FIXTURES_DIR / "test_signing_cert.pem").read_bytes()
    credential_set = await credentials.import_apple(
        tenant.id, "e2e-apple", key_pem, cert_pem
    )

    templates = TemplateService(session, objectstore)
    template = await templates.create_template(
        tenant.id, _TEMPLATE_KEY, "E2E Student ID", None
    )
    variant = await templates.create_variant(
        tenant.id,
        template.id,
        key="default",
        name="Default",
        wallet_type=WalletType.APPLE,
        is_default=True,
        credential_set_id=credential_set.id,
        google_class_id=None,
    )
    version = await templates.import_apple_version(
        tenant.id, variant.id, _make_apple_bundle()
    )
    await templates.set_mappings(
        tenant.id,
        version.id,
        [
            RuleSpec(
                target_kind=TargetKind.FIELD_VALUE,
                target="name",
                source_field="person.name",
                value_type=ValueType.TEXT,
                required=True,
            )
        ],
    )
    await templates.publish(tenant.id, version.id)

    async with httpx.AsyncClient(
        transport=_fake_data_provider_transport()
    ) as http_client:
        data_provider = DataProviderClient(
            base_url=settings.data_provider_base_url,
            token="",
            timeout=5.0,
            client=http_client,
        )
        render_service = RenderService(session, templates, credentials, data_provider)
        auth = AuthContext(
            client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
        )

        yield E2eEnv(settings=settings, service=render_service, auth=auth)
