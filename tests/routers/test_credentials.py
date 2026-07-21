"""Happy-path tests for the credentials router."""

from edutap.pass_builder.models.enums import Scope

from .conftest import seed_client

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
        "/api/v1/credentials",
        json={"provider": "apple", "label": "demo"},
        headers=creds_client.headers,
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("invalid_request")


async def test_create_google_credential_requires_issuer_and_account(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        "/api/v1/credentials",
        json={"provider": "google", "label": "demo"},
        headers=creds_client.headers,
    )
    assert response.status_code == 400


async def test_create_apple_credential_yields_key_pending(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        "/api/v1/credentials", json=_APPLE_BODY, headers=creds_client.headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "key_pending"
    assert body["provider"] == "apple"


async def test_create_google_credential_imports_service_account(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.post(
        "/api/v1/credentials",
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
            "/api/v1/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    response = await client.get(
        f"/api/v1/credentials/{created['id']}/csr", headers=creds_client.headers
    )
    assert response.status_code == 200
    assert "CERTIFICATE REQUEST" in response.text


async def test_list_credentials_filters_by_provider(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    await client.post(
        "/api/v1/credentials", json=_APPLE_BODY, headers=creds_client.headers
    )

    response = await client.get(
        "/api/v1/credentials?provider=google", headers=creds_client.headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_revoke_credential_marks_revoked_never_deleted(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            "/api/v1/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    delete_response = await client.delete(
        f"/api/v1/credentials/{created['id']}", headers=creds_client.headers
    )
    assert delete_response.status_code == 204

    listed = (
        await client.get("/api/v1/credentials", headers=creds_client.headers)
    ).json()
    [row] = [c for c in listed if c["id"] == created["id"]]
    assert row["status"] == "revoked"


async def test_renew_creates_successor_credential(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            "/api/v1/credentials", json=_APPLE_BODY, headers=creds_client.headers
        )
    ).json()

    response = await client.post(
        f"/api/v1/credentials/{created['id']}/renew", headers=creds_client.headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] != created["id"]
    assert body["status"] == "key_pending"


async def test_scope_manage_cannot_use_credentials_endpoints(client, session):
    manager = await seed_client(session, [Scope.MANAGE])

    response = await client.get("/api/v1/credentials", headers=manager.headers)
    assert response.status_code == 403
