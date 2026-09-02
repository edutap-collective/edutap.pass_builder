from sqlalchemy import select

from edutap.pass_builder.auth import hash_token, resolve_token
from edutap.pass_builder.models.db import ApiClient
from edutap.pass_builder.models.enums import Scope


async def make_tenant(ui, key: str = "lmu") -> dict:
    response = await ui.post("/tenants", json={"key": key, "name": "LMU München"})
    assert response.status_code == 201
    return response.json()


async def test_creating_a_tenant_and_listing_it_back(ui):
    tenant = await make_tenant(ui)
    assert tenant["key"] == "lmu"

    listed = await ui.get("/tenants")
    assert [row["id"] for row in listed.json()] == [tenant["id"]]


async def test_a_duplicate_key_is_409_not_an_integrity_error(ui):
    """`Tenant.key` is unique, and re-creating one is a plausible mistake."""
    await make_tenant(ui)
    response = await ui.post("/tenants", json={"key": "lmu", "name": "again"})
    assert response.status_code == 409
    assert response.json()["type"].endswith("tenant_exists")


async def test_a_created_client_authenticates_against_the_render_api(ui, session):
    """The whole point: the UI mints the credential nothing else can.

    Every render route resolves a bearer token against `api_client`, and no
    route there creates one. Without this, the first caller could never be
    authenticated at all.
    """
    tenant = await make_tenant(ui)
    response = await ui.post(
        f"/tenants/{tenant['id']}/clients",
        json={"name": "lmu_edutap_backend", "scopes": ["render"]},
    )
    assert response.status_code == 201
    token = response.json()["token"]

    context = await resolve_token(session, token)
    assert context.scopes == {Scope.RENDER}
    assert str(context.tenant_id) == tenant["id"]


async def test_only_the_hash_is_stored(ui, session):
    """A store that can show a token again can leak every token at once."""
    tenant = await make_tenant(ui)
    token = (
        await ui.post(
            f"/tenants/{tenant['id']}/clients",
            json={"name": "backend", "scopes": ["render"]},
        )
    ).json()["token"]

    row = (await session.execute(select(ApiClient))).scalar_one()
    assert row.token_hash == hash_token(token)
    assert token not in row.token_hash


async def test_listing_clients_never_returns_a_token(ui):
    tenant = await make_tenant(ui)
    await ui.post(
        f"/tenants/{tenant['id']}/clients",
        json={"name": "backend", "scopes": ["render"]},
    )

    listed = await ui.get(f"/tenants/{tenant['id']}/clients")

    assert listed.status_code == 200
    assert "token" not in listed.json()[0]


async def test_four_callers_get_four_distinct_tokens(ui):
    """One client per calling service, so `audit_log` can tell them apart."""
    tenant = await make_tenant(ui)
    tokens = set()
    for name in (
        "lmu_edutap_backend",
        "lmu_edutap_admin_backend",
        "wallet_apple_vas_account_binding",
        "wallet_apple_vas_web_service",
    ):
        response = await ui.post(
            f"/tenants/{tenant['id']}/clients",
            json={"name": name, "scopes": ["render"]},
        )
        tokens.add(response.json()["token"])
    assert len(tokens) == 4


async def test_revoking_deactivates_rather_than_deletes(ui, session):
    """`audit_log` references the client; a deleted row unattributes history."""
    tenant = await make_tenant(ui)
    created = (
        await ui.post(
            f"/tenants/{tenant['id']}/clients",
            json={"name": "backend", "scopes": ["render"]},
        )
    ).json()

    response = await ui.post(f"/tenants/{tenant['id']}/clients/{created['id']}/revoke")

    assert response.status_code == 204
    row = (await session.execute(select(ApiClient))).scalar_one()
    assert row.active is False


async def test_a_client_for_an_unknown_tenant_is_404(ui):
    response = await ui.post(
        "/tenants/00000000-0000-0000-0000-000000000000/clients",
        json={"name": "backend", "scopes": ["render"]},
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("tenant_not_found")
