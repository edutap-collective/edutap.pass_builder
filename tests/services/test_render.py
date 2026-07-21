"""Tests for the render service: projection, binding, delivery, audit."""

import base64
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from edutap.wallet_google.exceptions import ObjectAlreadyExistsException
from sqlalchemy import select
from sqlmodel import SQLModel

from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import (
    ApiClient,
    AuditLog,
    CredentialSet,
    DataField,
    MappingRule,
    Template,
    TemplateVariant,
    TemplateVersion,
    Tenant,
)
from edutap.pass_builder.models.enums import (
    Provider,
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


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


class FakeObjectStore:
    """In-memory object store (same shape as the one in test_templates.py)."""

    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.storage[key] = data

    async def get(self, key: str) -> bytes:
        return self.storage[key]


class FakeDataProvider:
    """Records the fields last requested; returns a configured response."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response: dict[str, Any] = {} if response is None else response
        self.last_fields: list[str] | None = None

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict[str, Any]:
        self.last_fields = fields
        return self.response


class FakeGoogleApi:
    """Records create/update/save_link calls instead of hitting the network."""

    def __init__(self, *, conflict_on_create: bool = False) -> None:
        self.created: list[tuple[Any, dict | None]] = []
        self.updated: list[tuple[Any, dict | None]] = []
        self.save_link_calls: list[tuple[list[Any], dict | None]] = []
        self._conflict_on_create = conflict_on_create

    def new(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"__model_name__": name, **data}

    async def acreate(self, data: Any, *, credentials: dict | None = None) -> Any:
        if self._conflict_on_create:
            raise ObjectAlreadyExistsException("already exists")
        self.created.append((data, credentials))
        return data

    async def aupdate(self, data: Any, *, credentials: dict | None = None) -> Any:
        self.updated.append((data, credentials))
        return data

    def save_link(self, models: list[Any], *, credentials: dict | None = None) -> str:
        self.save_link_calls.append((models, credentials))
        return "https://pay.google.com/gp/v/save/fake-jwt"


def _noop_sign(pkpass: object) -> None:
    """A no-op Apple signer: unit tests never sign with a real key."""


def _backend() -> DatabaseSecretBackend:
    return DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())


@dataclass
class RenderEnv:
    """Everything a render test needs: a tenant, an auth context, a service."""

    session: Any
    tenant_id: UUID
    auth: AuthContext
    service: RenderService
    data_provider: FakeDataProvider
    objectstore: FakeObjectStore = field(repr=False)
    list_audit: Callable[[], Awaitable[list[AuditLog]]] = field(repr=False)


async def _seed_tenant_and_client(session) -> tuple[Tenant, ApiClient]:
    tenant = Tenant(key=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    session.add(tenant)
    await session.flush()
    api_client = ApiClient(
        tenant_id=tenant.id,
        name="renderer",
        token_hash=f"unused-{uuid4().hex}",
        scopes=[Scope.RENDER],
    )
    session.add(api_client)
    await session.flush()
    return tenant, api_client


async def _seed_published_apple_template(session, tenant_id: UUID) -> TemplateVariant:
    """One published Apple template/variant/version, one rule: person.name."""
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
    return variant


async def _seed_published_google_template(
    session, tenant_id: UUID, backend: DatabaseSecretBackend
) -> tuple[TemplateVariant, CredentialSet]:
    """One published Google template/variant/version with a real credential set.

    `backend` must be the same instance later passed to `_make_service` --
    the secret material is sealed here and opened there, and each fresh
    `DatabaseSecretBackend` carries its own random master key.
    """
    credentials_service = CredentialService(session, backend)
    service_account = {
        "client_email": "svc@proj.iam.gserviceaccount.com",
        "private_key_id": "kid-1",
        "project_id": "proj",
        "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
    }
    credential_set = await credentials_service.import_google(
        tenant_id, "google-demo", json.dumps(service_account).encode(), issuer_id="3388"
    )

    template = Template(tenant_id=tenant_id, key="staff-id", name="Staff ID")
    session.add(template)
    await session.flush()

    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=WalletType.GOOGLE,
        key="staff",
        name="Staff",
        is_default=True,
        credential_set_id=credential_set.id,
        google_class_id="3388.staff",
    )
    session.add(variant)
    await session.flush()

    version = TemplateVersion(
        variant_id=variant.id,
        number=1,
        status=VersionStatus.PUBLISHED,
        class_json={"id": "3388.staff"},
        object_json={"cardTitle": {"defaultValue": {"value": "${person.name}"}}},
    )
    session.add(version)
    await session.flush()

    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    session.add(
        MappingRule(
            version_id=version.id,
            origin=RuleOrigin.AUTHORED,
            target_kind=TargetKind.JSON_POINTER,
            target="/cardTitle/defaultValue/value",
            source_field="person.name",
            value_type=ValueType.TEXT,
            required=True,
            position=0,
        )
    )
    await session.flush()
    return variant, credential_set


def _make_service(
    session,
    objectstore,
    data_provider,
    *,
    google_api=None,
    apple_sign=_noop_sign,
    backend: DatabaseSecretBackend | None = None,
) -> RenderService:
    templates = TemplateService(session, objectstore)
    credentials = CredentialService(session, backend or _backend())
    return RenderService(
        session,
        templates,
        credentials,
        data_provider,
        google_api=google_api,
        apple_sign=apple_sign,
    )


@pytest.fixture
async def render_env(session) -> RenderEnv:
    """A tenant with one published Apple template mapping `person.name`."""
    tenant, api_client = await _seed_tenant_and_client(session)
    await _seed_published_apple_template(session, tenant.id)

    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})
    service = _make_service(session, objectstore, data_provider)
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    async def list_audit() -> list[AuditLog]:
        query = (
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant.id)  # ty: ignore[invalid-argument-type]
            .order_by(AuditLog.ts)  # ty: ignore[invalid-argument-type]
        )
        return list((await session.execute(query)).scalars().all())

    return RenderEnv(
        session=session,
        tenant_id=tenant.id,
        auth=auth,
        service=service,
        data_provider=data_provider,
        objectstore=objectstore,
        list_audit=list_audit,
    )


# --- create_pass: Apple, projection and audit -------------------------------


async def test_create_apple_pass_requests_only_mapped_fields(render_env):
    """Only the fields a template maps are ever sent to the data provider."""
    env = render_env
    result = await env.service.create_pass(
        env.auth,
        pass_id="11111111-1111-1111-1111-111111111111",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="student-id",
        wallet_type=WalletType.APPLE,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )
    assert result.pkpass is not None
    assert env.data_provider.last_fields == ["person.name"]


async def test_create_apple_pass_writes_success_audit(render_env):
    """A successful render writes exactly one success audit entry."""
    env = render_env
    await env.service.create_pass(
        env.auth,
        pass_id="11111111-1111-1111-1111-111111111111",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="student-id",
        wallet_type=WalletType.APPLE,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )
    entries = await env.list_audit()
    assert entries[-1].outcome == "success"
    assert entries[-1].action == "pass.create"
    assert entries[-1].error_code is None
    assert entries[-1].requested_fields == ["person.name"]
    assert entries[-1].subject_ref == "u1"


async def test_missing_field_writes_error_audit_and_raises(render_env):
    """A missing required field raises 422 and audits the failure, no values."""
    env = render_env
    env.data_provider.response = {}  # person.name missing

    with pytest.raises(ProblemError) as excinfo:
        await env.service.create_pass(
            env.auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="student-id",
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid="u1",
            version_number=None,
        )

    assert excinfo.value.slug == "missing_field"
    entries = await env.list_audit()
    assert entries[-1].outcome == "error"
    assert entries[-1].error_code == "missing_field"
    assert "person.name" not in str(entries[-1].details)  # no field values leak


async def test_missing_field_error_lists_field_names_not_values(render_env):
    """The 422 body names the missing fields but never a data value."""
    env = render_env
    env.data_provider.response = {}

    with pytest.raises(ProblemError) as excinfo:
        await env.service.create_pass(
            env.auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="student-id",
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid="u1",
            version_number=None,
        )

    assert excinfo.value.extra["fields"] == ["person.name"]


async def test_unknown_template_writes_error_audit_with_no_ids(render_env):
    """A 404 before ids are resolved still writes an audit entry."""
    env = render_env
    with pytest.raises(ProblemError) as excinfo:
        await env.service.create_pass(
            env.auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="no-such-template",
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid="u1",
            version_number=None,
        )
    assert excinfo.value.slug == "template_not_found"

    entries = await env.list_audit()
    assert entries[-1].outcome == "error"
    assert entries[-1].error_code == "template_not_found"
    assert entries[-1].template_id is None
    assert entries[-1].requested_fields == []


# --- create_pass / update_pass: Google ---------------------------------------


async def test_create_google_pass_pushes_object_and_returns_ids(session):
    """Google create builds the object, pushes it, and returns object/class id."""
    tenant, api_client = await _seed_tenant_and_client(session)
    backend = _backend()
    variant, _credential_set = await _seed_published_google_template(
        session, tenant.id, backend
    )
    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})
    google_api = FakeGoogleApi()
    service = _make_service(
        session, objectstore, data_provider, google_api=google_api, backend=backend
    )
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    result = await service.create_pass(
        auth,
        pass_id="abc-uuid",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="staff-id",
        wallet_type=WalletType.GOOGLE,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )

    assert result.object_id == "3388.abc-uuid"
    assert result.class_id == variant.google_class_id
    assert len(google_api.created) == 1
    pushed_object, credentials = google_api.created[0]
    assert pushed_object["id"] == "3388.abc-uuid"
    assert credentials is not None
    assert credentials["client_email"] == "svc@proj.iam.gserviceaccount.com"


async def test_create_google_pass_treats_409_as_success(session):
    """An `ObjectAlreadyExistsException` on create is treated as success."""
    tenant, api_client = await _seed_tenant_and_client(session)
    backend = _backend()
    await _seed_published_google_template(session, tenant.id, backend)
    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})
    google_api = FakeGoogleApi(conflict_on_create=True)
    service = _make_service(
        session, objectstore, data_provider, google_api=google_api, backend=backend
    )
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    result = await service.create_pass(
        auth,
        pass_id="abc-uuid",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="staff-id",
        wallet_type=WalletType.GOOGLE,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )

    assert result.object_id == "3388.abc-uuid"


async def test_update_google_pass_uses_update_not_create(session):
    """update_pass calls the Google `update` path, not `create`."""
    tenant, api_client = await _seed_tenant_and_client(session)
    backend = _backend()
    await _seed_published_google_template(session, tenant.id, backend)
    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})
    google_api = FakeGoogleApi()
    service = _make_service(
        session, objectstore, data_provider, google_api=google_api, backend=backend
    )
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    await service.update_pass(
        auth,
        pass_id="abc-uuid",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="staff-id",
        wallet_type=WalletType.GOOGLE,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )

    assert len(google_api.updated) == 1
    assert google_api.created == []


# --- save_link -----------------------------------------------------------------


async def test_save_link_returns_google_save_url(session):
    """save_link builds a Reference and delegates to the Google api."""
    tenant, api_client = await _seed_tenant_and_client(session)
    backend = _backend()
    variant, _credential_set = await _seed_published_google_template(
        session, tenant.id, backend
    )
    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider()
    google_api = FakeGoogleApi()
    service = _make_service(
        session, objectstore, data_provider, google_api=google_api, backend=backend
    )
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    link = await service.save_link(
        auth,
        pass_id="abc-uuid",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="staff-id",
    )

    assert link == "https://pay.google.com/gp/v/save/fake-jwt"
    [(models, credentials)] = google_api.save_link_calls
    assert models[0]["id"] == "3388.abc-uuid"
    assert credentials is not None


async def test_save_link_raises_not_implemented_for_apple(render_env):
    """save_link has no Apple equivalent; it says so instead of guessing."""
    env = render_env
    with pytest.raises(NotImplementedError):
        await env.service.save_link(
            env.auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="student-id",
            wallet_type=WalletType.APPLE,
        )


# --- preview ---------------------------------------------------------------------


async def test_preview_does_not_call_data_provider_or_write_audit(render_env):
    """preview never hits the data provider and writes no audit entry."""
    env = render_env
    before = await env.list_audit()

    preview = await env.service.preview(
        env.auth,
        template_key="student-id",
        wallet_type=WalletType.APPLE,
        variant_key=None,
        version_number=None,
        sample_data={},
    )

    assert env.data_provider.last_fields is None
    assert preview["bound_fields"] == ["person.name"]
    assert preview["pass_json"]["generic"]["primaryFields"][0]["value"]
    after = await env.list_audit()
    assert after == before


async def test_preview_uses_provided_sample_data_over_placeholder(render_env):
    """Sample data, when given, wins over the generated placeholder."""
    env = render_env

    preview = await env.service.preview(
        env.auth,
        template_key="student-id",
        wallet_type=WalletType.APPLE,
        variant_key=None,
        version_number=None,
        sample_data={"person.name": "Grace Hopper"},
    )

    value = preview["pass_json"]["generic"]["primaryFields"][0]["value"]
    assert value == "Grace Hopper"


# --- unexpected failures / tenant-scoped credentials --------------------------


async def test_unexpected_error_writes_audit_and_becomes_500(session):
    """A non-`ProblemError` failure still audits and surfaces as a 500.

    Any exception during resolve->fetch->bind->build/push -- not just a
    `ProblemError` -- must leave an audit trail and never leak the original
    exception message (it could carry secret/PII material) to the caller or
    the audit entry.
    """
    tenant, api_client = await _seed_tenant_and_client(session)
    await _seed_published_apple_template(session, tenant.id)
    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})

    def _boom_sign(pkpass: object) -> None:
        raise RuntimeError("boom")

    service = _make_service(session, objectstore, data_provider, apple_sign=_boom_sign)
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )

    with pytest.raises(ProblemError) as excinfo:
        await service.create_pass(
            auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="student-id",
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid="u1",
            version_number=None,
        )

    assert excinfo.value.status == 500
    assert excinfo.value.slug == "internal_error"
    assert "boom" not in (excinfo.value.detail or "")

    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant.id)  # ty: ignore[invalid-argument-type]
        .order_by(AuditLog.ts)  # ty: ignore[invalid-argument-type]
    )
    entries = list((await session.execute(query)).scalars().all())
    last = entries[-1]
    assert last.outcome == "error"
    assert last.error_code == "internal_error"
    assert "boom" not in str(last.error_code)
    assert "boom" not in str(last.details)
    assert "boom" not in str(last.requested_fields)
    assert "boom" not in str(last.subject_ref)


async def test_credential_from_other_tenant_is_not_used(session):
    """A variant's `credential_set_id` from another tenant must not resolve.

    The credential set lookup on the render path is tenant-scoped, so even
    if a variant's `credential_set_id` happens to reference a credential set
    belonging to a different tenant, rendering must fail closed with a
    generic 404 rather than sign with someone else's key material.
    """
    tenant_a, api_client_a = await _seed_tenant_and_client(session)
    variant = await _seed_published_apple_template(session, tenant_a.id)

    tenant_b, _api_client_b = await _seed_tenant_and_client(session)
    credential_set_b = CredentialSet(
        tenant_id=tenant_b.id, provider=Provider.APPLE, label="tenant-b-cred"
    )
    session.add(credential_set_b)
    await session.flush()

    variant.credential_set_id = credential_set_b.id
    session.add(variant)
    await session.flush()

    objectstore = FakeObjectStore()
    data_provider = FakeDataProvider(response={"person.name": "Ada Lovelace"})
    # No `apple_sign` override: the override would short-circuit the
    # credential lookup this test exists to exercise.
    service = _make_service(session, objectstore, data_provider, apple_sign=None)
    auth = AuthContext(
        client_id=api_client_a.id, tenant_id=tenant_a.id, scopes={Scope.RENDER}
    )

    with pytest.raises(ProblemError) as excinfo:
        await service.create_pass(
            auth,
            pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
            template_key="student-id",
            wallet_type=WalletType.APPLE,
            variant_key=None,
            person_uid="u1",
            version_number=None,
        )

    assert excinfo.value.status == 404
    assert excinfo.value.slug == "credential_not_found"
