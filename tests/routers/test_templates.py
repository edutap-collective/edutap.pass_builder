"""Happy-path and lifecycle tests for the templates router.

Includes the mandatory immutability case (spec section 7, case 3): any
modification of a `published` version -- mappings or assets -- must yield
`409`.
"""

from uuid import UUID

from sqlalchemy import select

from edutap.pass_builder.dependencies import get_template_service
from edutap.pass_builder.models.db import (
    AuditLog,
    DataField,
    Template,
    TemplateVariant,
    TemplateVersion,
)
from edutap.pass_builder.models.enums import Scope, ValueType, VersionStatus, WalletType
from edutap.pass_builder.services.templates import TemplateService

from .conftest import make_apple_bundle, seed_client

_A_RULE = {
    "target_kind": "field_value",
    "target": "name",
    "source_field": "person.name",
    "value_type": "text",
}


async def _create_template_and_variant(client, headers) -> tuple[str, str]:
    """Create a template and an Apple variant through the API. Returns ids."""
    template = (
        await client.post(
            "/api/v1/templates",
            json={"key": "student-id", "name": "Student ID"},
            headers=headers,
        )
    ).json()
    variant = (
        await client.post(
            f"/api/v1/templates/{template['id']}/variants",
            json={
                "key": "student",
                "name": "Student",
                "wallet_type": "apple",
                "is_default": True,
            },
            headers=headers,
        )
    ).json()
    return template["id"], variant["id"]


async def _import_apple_version(client, headers, variant_id: str) -> str:
    """Import a draft Apple version via multipart upload. Returns its id."""
    response = await client.post(
        f"/api/v1/variants/{variant_id}/versions",
        files={
            "file": ("bundle.pkpasstemplate", make_apple_bundle(), "application/zip")
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- templates CRUD ----------------------------------------------------------


async def test_create_and_get_template(client, session):
    manager = await seed_client(session, [Scope.MANAGE])

    created = await client.post(
        "/api/v1/templates",
        json={"key": "student-id", "name": "Student ID", "description": "desc"},
        headers=manager.headers,
    )
    assert created.status_code == 201
    template_id = created.json()["id"]

    fetched = await client.get(
        f"/api/v1/templates/{template_id}", headers=manager.headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["key"] == "student-id"


async def test_list_templates_only_shows_own_tenant(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    other = await seed_client(session, [Scope.MANAGE])
    await client.post(
        "/api/v1/templates",
        json={"key": "a", "name": "A"},
        headers=manager.headers,
    )

    response = await client.get("/api/v1/templates", headers=other.headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_patch_template_updates_name(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    template_id, _variant_id = await _create_template_and_variant(
        client, manager.headers
    )

    response = await client.patch(
        f"/api/v1/templates/{template_id}",
        json={"name": "Renamed"},
        headers=manager.headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


async def test_archive_template_sets_archived_at(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    template_id, _variant_id = await _create_template_and_variant(
        client, manager.headers
    )

    response = await client.delete(
        f"/api/v1/templates/{template_id}", headers=manager.headers
    )
    assert response.status_code == 200
    assert response.json()["archived_at"] is not None


async def test_scope_render_cannot_manage_templates(client, session):
    renderer = await seed_client(session, [Scope.RENDER])

    response = await client.post(
        "/api/v1/templates", json={"key": "a", "name": "A"}, headers=renderer.headers
    )
    assert response.status_code == 403


async def test_missing_token_is_unauthenticated(client, session):
    response = await client.get("/api/v1/templates")
    assert response.status_code == 401


# --- variants ------------------------------------------------------------------


async def test_create_and_patch_variant(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )

    fetched = await client.get(
        f"/api/v1/variants/{variant_id}", headers=manager.headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["is_default"] is True

    patched = await client.patch(
        f"/api/v1/variants/{variant_id}",
        json={"name": "Renamed Variant"},
        headers=manager.headers,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed Variant"


async def test_setting_default_unsets_previous_default(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    template_id, first_variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    second = (
        await client.post(
            f"/api/v1/templates/{template_id}/variants",
            json={
                "key": "staff",
                "name": "Staff",
                "wallet_type": "apple",
                "is_default": True,
            },
            headers=manager.headers,
        )
    ).json()

    first = (
        await client.get(
            f"/api/v1/variants/{first_variant_id}", headers=manager.headers
        )
    ).json()
    assert first["is_default"] is False
    assert second["is_default"] is True


# --- versions, mappings, publish -----------------------------------------------


async def test_import_apple_version_splits_pass_json(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )

    version_id = await _import_apple_version(client, manager.headers, variant_id)

    fetched = await client.get(
        f"/api/v1/versions/{version_id}", headers=manager.headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "draft"


async def test_set_and_get_mappings(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    version_id = await _import_apple_version(client, manager.headers, variant_id)
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()

    put_response = await client.put(
        f"/api/v1/versions/{version_id}/mappings",
        json={"rules": [_A_RULE]},
        headers=manager.headers,
    )
    assert put_response.status_code == 200
    assert put_response.json()["rules"][0]["source_field"] == "person.name"

    get_response = await client.get(
        f"/api/v1/versions/{version_id}/mappings", headers=manager.headers
    )
    assert get_response.json()["rules"][0]["source_field"] == "person.name"


async def test_validate_reports_findings_without_publishing(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    upload = await client.post(
        f"/api/v1/variants/{variant_id}/versions",
        files={
            "file": (
                "bundle.pkpasstemplate",
                make_apple_bundle(include_icon=False),
                "application/zip",
            )
        },
        headers=manager.headers,
    )
    version_id = upload.json()["id"]

    response = await client.post(
        f"/api/v1/versions/{version_id}/validate", headers=manager.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["findings"]

    unchanged = await client.get(
        f"/api/v1/versions/{version_id}", headers=manager.headers
    )
    assert unchanged.json()["status"] == "draft"


async def _publish_valid_version(client, headers, variant_id: str) -> str:
    """Import, map and publish a version that passes validation. Returns its id."""
    version_id = await _import_apple_version(client, headers, variant_id)
    await client.put(
        f"/api/v1/versions/{version_id}/mappings",
        json={"rules": [_A_RULE]},
        headers=headers,
    )
    response = await client.post(
        f"/api/v1/versions/{version_id}/publish", headers=headers
    )
    assert response.status_code == 200, response.text
    return version_id


async def test_publish_makes_version_published_and_archives_predecessor(
    client, session
):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()

    first_version_id = await _publish_valid_version(client, manager.headers, variant_id)
    second_version_id = await _import_apple_version(client, manager.headers, variant_id)
    await client.put(
        f"/api/v1/versions/{second_version_id}/mappings",
        json={"rules": [_A_RULE]},
        headers=manager.headers,
    )
    publish_response = await client.post(
        f"/api/v1/versions/{second_version_id}/publish", headers=manager.headers
    )
    assert publish_response.status_code == 200

    first = (
        await client.get(
            f"/api/v1/versions/{first_version_id}", headers=manager.headers
        )
    ).json()
    assert first["status"] == "archived"


# --- immutability (mandatory) ---------------------------------------------------


async def test_modifying_published_mappings_is_409(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()
    version_id = await _publish_valid_version(client, manager.headers, variant_id)

    response = await client.put(
        f"/api/v1/versions/{version_id}/mappings",
        json={"rules": []},
        headers=manager.headers,
    )
    assert response.status_code == 409


async def test_modifying_published_asset_is_409(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()
    version_id = await _publish_valid_version(client, manager.headers, variant_id)

    put_response = await client.put(
        f"/api/v1/versions/{version_id}/assets/icon.png",
        files={"file": ("icon.png", b"\x89PNG-new", "image/png")},
        headers=manager.headers,
    )
    assert put_response.status_code == 409

    delete_response = await client.delete(
        f"/api/v1/versions/{version_id}/assets/icon.png", headers=manager.headers
    )
    assert delete_response.status_code == 409


async def test_get_asset_returns_bytes(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    version_id = await _import_apple_version(client, manager.headers, variant_id)

    response = await client.get(
        f"/api/v1/versions/{version_id}/assets/icon.png", headers=manager.headers
    )
    assert response.status_code == 200
    assert response.content == b"\x89PNG"


# --- audit -------------------------------------------------------------------


async def test_publish_is_audited(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()

    version_id = await _publish_valid_version(client, manager.headers, variant_id)

    rows = (
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id  # ty: ignore[invalid-argument-type]
                    == manager.tenant_id,
                    AuditLog.action  # ty: ignore[invalid-argument-type]
                    == "template.publish",
                )
            )
        )
        .scalars()
        .all()
    )
    [entry] = rows
    assert entry.outcome == "success"
    assert entry.template_id == UUID(template_id)
    assert entry.variant_id == UUID(variant_id)
    assert entry.version_id == UUID(version_id)


async def test_publish_failure_is_audited_as_error(client, session):
    """A version that fails publish validation is audited as an error.

    Reuses `make_apple_bundle(include_icon=False)` (already exercised by
    `test_validate_reports_findings_without_publishing`) to fail publish
    validation with `missing required asset: icon.png` -- no mapping rules
    are needed for that finding to fire. Asserts the response is the
    `422 template_validation_failed` problem, and that the router's
    `audited()` guard (spec section 6) still wrote a matching
    `outcome="error"` audit row for `template.publish`.
    """
    manager = await seed_client(session, [Scope.MANAGE])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    upload = await client.post(
        f"/api/v1/variants/{variant_id}/versions",
        files={
            "file": (
                "bundle.pkpasstemplate",
                make_apple_bundle(include_icon=False),
                "application/zip",
            )
        },
        headers=manager.headers,
    )
    version_id = upload.json()["id"]

    response = await client.post(
        f"/api/v1/versions/{version_id}/publish", headers=manager.headers
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("template_validation_failed")

    rows = (
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id  # ty: ignore[invalid-argument-type]
                    == manager.tenant_id,
                    AuditLog.action  # ty: ignore[invalid-argument-type]
                    == "template.publish",
                )
            )
        )
        .scalars()
        .all()
    )
    [entry] = rows
    assert entry.outcome == "error"
    assert entry.error_code == "template_validation_failed"
    assert entry.version_id == UUID(version_id)
    assert entry.requested_fields == []


# --- variant sync --------------------------------------------------------------


class FakeGoogleApi:
    """Records `Class` push calls instead of hitting the network.

    Same shape as `tests/services/test_render.py`'s `FakeGoogleApi`, kept
    local here since router tests wire it in through a dependency override
    rather than `TemplateService`'s constructor.
    """

    def __init__(self) -> None:
        self.created: list[tuple[dict, dict | None]] = []

    def new(self, name: str, data: dict) -> dict:
        return {"__model_name__": name, **data}

    async def acreate(self, data: dict, *, credentials: dict | None = None) -> dict:
        self.created.append((data, credentials))
        return data

    async def aupdate(self, data: dict, *, credentials: dict | None = None) -> dict:
        return data


async def _seed_google_variant(
    client, session, headers, tenant_id
) -> tuple[UUID, UUID]:
    """Seed a published Google variant with a real credential set. Returns ids."""
    credential = (
        await client.post(
            "/api/v1/credentials",
            json={
                "provider": "google",
                "label": "google-demo",
                "issuer_id": "3388",
                "service_account_json": {
                    "client_email": "svc@proj.iam.gserviceaccount.com",
                    "private_key_id": "kid-1",
                    "project_id": "proj",
                    "private_key": (
                        "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
                    ),
                },
            },
            headers=headers,
        )
    ).json()

    template = Template(tenant_id=tenant_id, key="staff-id", name="Staff ID")
    session.add(template)
    await session.flush()
    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=WalletType.GOOGLE,
        key="staff",
        name="Staff",
        is_default=True,
        credential_set_id=UUID(credential["id"]),
        google_class_id="3388.staff",
    )
    session.add(variant)
    await session.flush()
    session.add(
        TemplateVersion(
            variant_id=variant.id,
            number=1,
            status=VersionStatus.PUBLISHED,
            class_json={"id": "3388.staff"},
            object_json={"id": "3388.staff.{{pass_id}}"},
        )
    )
    await session.flush()
    return variant.id, UUID(credential["id"])


async def test_variant_sync_endpoint(client, session, objectstore, app):
    manager = await seed_client(session, [Scope.MANAGE, Scope.CREDENTIALS])
    variant_id, _credential_id = await _seed_google_variant(
        client, session, manager.headers, manager.tenant_id
    )

    fake_google = FakeGoogleApi()
    app.dependency_overrides[get_template_service] = lambda: TemplateService(
        session, objectstore, google_api=fake_google
    )

    response = await client.post(
        f"/api/v1/variants/{variant_id}/sync", headers=manager.headers
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "synced"}
    assert len(fake_google.created) == 1

    rows = (
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id  # ty: ignore[invalid-argument-type]
                    == manager.tenant_id,
                    AuditLog.action  # ty: ignore[invalid-argument-type]
                    == "variant.sync",
                )
            )
        )
        .scalars()
        .all()
    )
    [entry] = rows
    assert entry.outcome == "success"
    assert entry.variant_id == variant_id


async def test_variant_sync_rejects_non_google_variant_before_decrypting(
    client, session
):
    """A non-Google variant is rejected before any credential is opened.

    The attached credential set is Apple's (PEM private key, not JSON): if
    the handler ever decrypted before checking `wallet_type`, the
    `json.loads` on that PEM would blow up instead of yielding a clean
    `400`. This is the ordering guarantee from spec section 6 in test form.
    """
    manager = await seed_client(session, [Scope.MANAGE, Scope.CREDENTIALS])
    _template_id, variant_id = await _create_template_and_variant(
        client, manager.headers
    )
    credential = (
        await client.post(
            "/api/v1/credentials",
            json={
                "provider": "apple",
                "label": "demo",
                "common_name": "Pass Type ID: pass.demo.lmu.de",
            },
            headers=manager.headers,
        )
    ).json()
    patch_response = await client.patch(
        f"/api/v1/variants/{variant_id}",
        json={"credential_set_id": credential["id"]},
        headers=manager.headers,
    )
    assert patch_response.status_code == 200

    response = await client.post(
        f"/api/v1/variants/{variant_id}/sync", headers=manager.headers
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("not_a_google_variant")
