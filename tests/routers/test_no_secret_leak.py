"""Mandatory cross-cutting test: no endpoint ever returns secret material.

Covers both credential flavours: Apple's internally-generated private key
(never returned by any endpoint, so its exact bytes cannot even be asserted
against a response -- the test instead asserts the universal marker "PRIVATE
KEY" never appears) and an imported Google service account, whose JSON
payload carries a private key the test knows verbatim.
"""

from edutap.pass_builder.models.enums import Scope

from .conftest import seed_client

_KNOWN_PRIVATE_KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\nMIIFAKEKEYMATERIALFAKEKEY\n"
    "-----END PRIVATE KEY-----\n"
)
_SERVICE_ACCOUNT = {
    "client_email": "svc@proj.iam.gserviceaccount.com",
    "private_key_id": "kid-1",
    "project_id": "proj",
    "private_key": _KNOWN_PRIVATE_KEY_PEM,
}


def _assert_no_secret(body: str) -> None:
    assert "PRIVATE KEY" not in body
    assert _KNOWN_PRIVATE_KEY_PEM not in body
    assert "FAKEKEYMATERIAL" not in body


async def test_no_endpoint_returns_apple_key_material(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = await client.post(
        "/api/v1/credentials",
        json={
            "provider": "apple",
            "label": "demo",
            "common_name": "Pass Type ID: pass.demo.lmu.de",
        },
        headers=creds_client.headers,
    )
    _assert_no_secret(created.text)
    credential_id = created.json()["id"]

    for path in (
        "/api/v1/credentials",
        f"/api/v1/credentials/{credential_id}/csr",
    ):
        response = await client.get(path, headers=creds_client.headers)
        _assert_no_secret(response.text)

    renewed = await client.post(
        f"/api/v1/credentials/{credential_id}/renew", headers=creds_client.headers
    )
    _assert_no_secret(renewed.text)


async def test_no_endpoint_returns_google_service_account_key(client, session):
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = await client.post(
        "/api/v1/credentials",
        json={
            "provider": "google",
            "label": "google-demo",
            "issuer_id": "3388",
            "service_account_json": _SERVICE_ACCOUNT,
        },
        headers=creds_client.headers,
    )
    _assert_no_secret(created.text)
    credential_id = created.json()["id"]

    listed = await client.get("/api/v1/credentials", headers=creds_client.headers)
    _assert_no_secret(listed.text)

    revoked = await client.delete(
        f"/api/v1/credentials/{credential_id}", headers=creds_client.headers
    )
    _assert_no_secret(revoked.text)

    listed_again = await client.get("/api/v1/credentials", headers=creds_client.headers)
    _assert_no_secret(listed_again.text)


async def test_credential_response_schema_excludes_secret_fields(client, session):
    """Belt and braces: even the raw JSON keys of a credential are checked."""
    creds_client = await seed_client(session, [Scope.CREDENTIALS])
    created = (
        await client.post(
            "/api/v1/credentials",
            json={
                "provider": "google",
                "label": "google-demo",
                "issuer_id": "3388",
                "service_account_json": _SERVICE_ACCOUNT,
            },
            headers=creds_client.headers,
        )
    ).json()

    forbidden_keys = {
        "private_key",
        "service_account_json",
        "ciphertext",
        "nonce",
        "wrapped_dek",
    }
    assert forbidden_keys.isdisjoint(created.keys())
