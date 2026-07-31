"""Happy-path tests for the credentials router."""

import json
from pathlib import Path

from sqlalchemy import select

from edutap.pass_builder.app import API_PREFIX
from edutap.pass_builder.models.db import AuditLog
from edutap.pass_builder.models.enums import Scope

from .conftest import seed_client

_APPLE_CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"
_TEST_KEY = Path(__file__).parent.parent / "fixtures" / "test_signing_key.pem"
_TEST_CERT = Path(__file__).parent.parent / "fixtures" / "test_signing_cert.pem"

_SERVICE_ACCOUNT = {
    "client_email": "svc@proj.iam.gserviceaccount.com",
    "private_key_id": "kid-1",
    "project_id": "proj",
    "private_key": "-----BEGIN PRIVATE KEY-----\nFAKEFAKE\n-----END PRIVATE KEY-----\n",
}

_APPLE_BODY = {
    "provider": "apple",
    "label": "demo",
    "common_name": "Pass Type ID: pass.demo.lmu.de",
}


async def test_create_apple_credential_requires_common_name(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials",
        json={"provider": "apple", "label": "demo"},
        headers=creds_client.headers,
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("invalid_request")


async def test_create_google_credential_requires_issuer_and_account(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials",
        json={"provider": "google", "label": "demo"},
        headers=creds_client.headers,
    )
    assert response.status_code == 400


async def test_create_apple_credential_yields_key_pending(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "key_pending"
    assert body["provider"] == "apple"


async def test_import_apple_credential_with_key_and_cert(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials",
        json={
            "provider": "apple",
            "label": "imported",
            "private_key": _TEST_KEY.read_text(),
            "certificate": _TEST_CERT.read_text(),
        },
        headers=creds_client.headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active"
    assert body["pass_type_identifier"] == "pass.test.local"  # noqa: S105
    # the imported key material must never appear in the response
    assert "PRIVATE KEY" not in response.text


async def test_import_apple_credential_rejects_mismatched_cert(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials",
        json={
            "provider": "apple",
            "label": "mismatch",
            "private_key": _TEST_KEY.read_text(),
            "certificate": _APPLE_CERT.read_text(),
        },
        headers=creds_client.headers,
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("certificate_key_mismatch")


async def test_import_apple_credential_is_audited(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    await client.post(
        f"{API_PREFIX}/credentials",
        json={
            "provider": "apple",
            "label": "imported",
            "private_key": _TEST_KEY.read_text(),
            "certificate": _TEST_CERT.read_text(),
        },
        headers=creds_client.headers,
    )
    entries = (await session.execute(select(AuditLog))).scalars().all()
    actions = {entry.action for entry in entries}
    assert "credential.create" in actions
    for entry in entries:
        assert "PRIVATE KEY" not in str(entry.details)


async def test_create_google_credential_imports_service_account(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials",
        json={
            "provider": "google",
            "label": "google-demo",
            "issuer_id": "3388",
            "service_account_json": _SERVICE_ACCOUNT,
        },
        headers=creds_client.headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active"
    assert body["service_account_email"] == "svc@proj.iam.gserviceaccount.com"


async def test_get_csr_returns_pem(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    response = await client.get(
        f"{API_PREFIX}/credentials/{created['id']}/csr", headers=creds_client.headers
    )
    assert response.status_code == 200
    assert "CERTIFICATE REQUEST" in response.text


async def test_list_credentials_filters_by_provider(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    await client.post(
        f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
    )

    response = await client.get(
        f"{API_PREFIX}/credentials?provider=google", headers=creds_client.headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_revoke_credential_marks_revoked_never_deleted(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    delete_response = await client.delete(
        f"{API_PREFIX}/credentials/{created['id']}", headers=creds_client.headers
    )
    assert delete_response.status_code == 204

    listed = (
        await client.get(f"{API_PREFIX}/credentials", headers=creds_client.headers)
    ).json()
    [row] = [c for c in listed if c["id"] == created["id"]]
    assert row["status"] == "revoked"


async def test_renew_creates_successor_credential(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    response = await client.post(
        f"{API_PREFIX}/credentials/{created['id']}/renew", headers=creds_client.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] != created["id"]
    assert body["status"] == "key_pending"


async def test_scope_manage_cannot_use_credentials_endpoints(client, session):
    manager = await seed_client(session, [Scope.MANAGE])

    response = await client.get(f"{API_PREFIX}/credentials", headers=manager.headers)
    assert response.status_code == 403


async def test_credential_create_is_audited(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
    )
    assert response.status_code == 201

    rows = (
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id  # ty: ignore[invalid-argument-type]
                    == creds_client.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    [entry] = rows
    assert entry.action == "credential.create"
    assert entry.outcome == "success"
    assert entry.error_code is None
    assert entry.requested_fields == []

    # No secret material -- the private key never leaves `open_material` --
    # can end up in an audit row: serialize the whole entry and check for
    # PEM markers rather than trusting any single column.
    serialized = json.dumps(entry.model_dump(mode="json"))
    assert "PRIVATE KEY" not in serialized
    assert "BEGIN" not in serialized


async def test_credential_certificate_mismatch_is_audited_as_error(client, session):
    """Installing a certificate that does not match the stored key.

    `apple_cert.pem` never matches a freshly generated key (mirrors
    `tests/services/test_credentials.py::test_install_mismatched_certificate_is_rejected`),
    so this exercises the `credential.certificate_installed` failure path:
    the response is the `409 certificate_key_mismatch` problem, and the
    router's `audited()` guard (spec section 6) must still have written a
    matching `outcome="error"` audit row.
    """
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            f"{API_PREFIX}/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    response = await client.put(
        f"{API_PREFIX}/credentials/{created['id']}/certificate",
        json={"certificate_pem": _APPLE_CERT.read_text()},
        headers=creds_client.headers,
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("certificate_key_mismatch")

    rows = (
        (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id  # ty: ignore[invalid-argument-type]
                    == creds_client.tenant_id,
                    AuditLog.action  # ty: ignore[invalid-argument-type]
                    == "credential.certificate_installed",
                )
            )
        )
        .scalars()
        .all()
    )
    [entry] = rows
    assert entry.outcome == "error"
    assert entry.error_code == "certificate_key_mismatch"
    assert entry.requested_fields == []
    assert entry.subject_ref is None

    # No secret material in the error row either.
    serialized = json.dumps(entry.model_dump(mode="json"))
    assert "PRIVATE KEY" not in serialized
    assert "BEGIN" not in serialized
