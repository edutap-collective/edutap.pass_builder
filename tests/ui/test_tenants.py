from sqlalchemy import select

from edutap.pass_builder.auth import hash_token, resolve_token
from edutap.pass_builder.models.db import ApiClient
from edutap.pass_builder.models.enums import Scope
from edutap.pass_builder.services.tenants import DeclaredTenant, reconcile_tenants


async def make_tenant(ui, session, key: str = "lmu") -> dict:
    """Seed a tenant the way the service gets one: declared, then reconciled.

    There is no POST /tenants any more -- tenants come from settings -- so a
    test that needs one goes through the same reconciliation the lifespan
    runs, and reads it back through the interface like a person would.
    """
    await reconcile_tenants(session, [DeclaredTenant(key=key, name="LMU München")])
    listed = await ui.get("/tenants")
    return next(t for t in listed.json() if t["key"] == key)


async def test_declared_tenants_are_listed_with_their_state(ui, session):
    """What the interface lists is the outcome of the reconciliation."""
    await reconcile_tenants(
        session,
        [
            DeclaredTenant(key="lmu", name="LMU München"),
            DeclaredTenant(key="stwm", name="Studierendenwerk"),
        ],
    )
    await reconcile_tenants(session, [DeclaredTenant(key="lmu", name="LMU München")])
    listed = {t["key"]: t for t in (await ui.get("/tenants")).json()}
    assert listed["lmu"]["active"] is True
    assert listed["stwm"]["active"] is False, "left the declaration: still listed"


async def test_there_is_no_way_to_create_a_tenant_here(ui):
    """Tenants are declared in settings. A POST is not a mistake to tolerate
    with a 409; it is a route that does not exist."""
    response = await ui.post("/tenants", json={"key": "lmu", "name": "x"})
    assert response.status_code == 405


async def test_a_created_client_authenticates_against_the_render_api(ui, session):
    """The whole point: the UI mints the credential nothing else can.

    Every render route resolves a bearer token against `api_client`, and no
    route there creates one. Without this, the first caller could never be
    authenticated at all.
    """
    tenant = await make_tenant(ui, session)
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
    tenant = await make_tenant(ui, session)
    token = (
        await ui.post(
            f"/tenants/{tenant['id']}/clients",
            json={"name": "backend", "scopes": ["render"]},
        )
    ).json()["token"]

    row = (await session.execute(select(ApiClient))).scalar_one()
    assert row.token_hash == hash_token(token)
    assert token not in row.token_hash


async def test_listing_clients_never_returns_a_token(ui, session):
    tenant = await make_tenant(ui, session)
    await ui.post(
        f"/tenants/{tenant['id']}/clients",
        json={"name": "backend", "scopes": ["render"]},
    )

    listed = await ui.get(f"/tenants/{tenant['id']}/clients")

    assert listed.status_code == 200
    assert "token" not in listed.json()[0]


async def test_four_callers_get_four_distinct_tokens(ui, session):
    """One client per calling service, so `audit_log` can tell them apart."""
    tenant = await make_tenant(ui, session)
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
    tenant = await make_tenant(ui, session)
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
