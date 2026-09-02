"""The UI reaches the management routers themselves, not copies of them."""

from sqlalchemy import select

from edutap.pass_builder.models.db import AuditLog

from .conftest import AUTHORISED
from .test_tenants import make_tenant


async def test_a_template_is_created_through_the_reused_router(ui):
    """`routers/templates.create_template`, mounted under a tenant path.

    Nothing about it was rewritten for the UI -- the same body, the same
    service call, the same 201.
    """
    tenant = await make_tenant(ui)

    response = await ui.post(
        f"/tenants/{tenant['id']}/templates",
        json={"key": "esc_id_v1", "name": "European Student Card"},
    )

    assert response.status_code == 201
    assert response.json()["key"] == "esc_id_v1"


async def test_the_audit_entry_names_the_person(ui, session):
    """The reason `actor_principal` exists, on the action that needs it most.

    `actor_client_id` is a foreign key into `api_client`, so it can only name
    a machine. Without a second column, creating a signing credential would
    have been recorded with no actor at all -- and a NULL there reads exactly
    like an entry whose actor was never captured.
    """
    tenant = await make_tenant(ui)
    response = await ui.post(
        f"/tenants/{tenant['id']}/credentials",
        json={
            "provider": "apple",
            "label": "LMU pass type id",
            "common_name": "pass.de.lmu.wallet",
        },
    )
    assert response.status_code == 201

    query = select(AuditLog).order_by(
        AuditLog.ts  # ty: ignore[invalid-argument-type]
    )
    entries = (await session.execute(query)).scalars().all()

    assert [entry.action for entry in entries] == ["credential.create"]
    assert entries[0].actor_principal == AUTHORISED
    assert entries[0].actor_client_id is None


async def test_an_unknown_tenant_is_404_before_the_router_body_runs(ui):
    tenant_id = "00000000-0000-0000-0000-000000000000"
    response = await ui.post(
        f"/tenants/{tenant_id}/templates",
        json={"key": "esc_id_v1", "name": "European Student Card"},
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("tenant_not_found")


async def test_a_malformed_tenant_segment_answers_like_an_unknown_one(ui):
    """404, the same as a tenant id that simply does not exist.

    GUARANTEED, NOT MEASURED, and that is the whole point of declaring
    `tenant_path_parameter` as a `str`: typed as a `UUID` it could reject the
    segment itself with a `422`, and which of the two answers came back would
    depend on the order FastAPI happened to solve dependencies in. Parsing
    lives in `ui_auth_context` alone, so nothing can get in front of it.

    And 404 for both is the property worth having: neither answer says
    anything about which tenant ids are real.
    """
    response = await ui.post(
        "/tenants/not-a-uuid/templates",
        json={"key": "esc_id_v1", "name": "European Student Card"},
    )
    assert response.status_code == 404


async def test_an_unlisted_principal_reaches_no_management_route(ui_app, session):
    from httpx import ASGITransport, AsyncClient

    from edutap.pass_builder.ui.app import UI_PREFIX

    transport = ASGITransport(app=ui_app)
    async with AsyncClient(
        transport=transport,
        base_url=f"http://ui{UI_PREFIX}",
        headers={"REMOTE_USER": "stranger@example.org"},
    ) as client:
        tenant_id = "00000000-0000-0000-0000-000000000000"
        response = await client.post(
            f"/tenants/{tenant_id}/templates", json={"key": "x", "name": "x"}
        )

    # 403 and not 404: the principal is refused before the tenant is resolved,
    # so an outsider cannot use the difference between the two answers to find
    # out which tenant ids exist.
    assert response.status_code == 403


def test_the_ui_cannot_render_a_pass(ui_app):
    """`passes.router` is deliberately not mounted here.

    Rendering a person's pass is not a management action, and keeping it out
    means this application never carries the one route whose zone matters.
    """
    paths = set(ui_app.openapi()["paths"])

    # Read off the OpenAPI document rather than the route table: it is what a
    # generated client sees, and it is the thing that would be wrong if the
    # render routes leaked in here.
    assert any(path.endswith("/templates") for path in paths), paths
    assert not any(path.endswith("/passes") for path in paths), paths


async def test_the_single_page_application_does_not_shadow_the_api(ui, ui_app):
    """The mount at "/" is added last, and order is what keeps it harmless.

    Starlette matches routes in the order they were added, so a mount at the
    root added before the routers would swallow every API path. This only
    exercises anything once the frontend has been built -- `make
    build-frontend` or the Docker stage -- and is a no-op otherwise, which is
    deliberate: the Python half must stay runnable without a Node toolchain.
    """
    from httpx import ASGITransport, AsyncClient

    from edutap.pass_builder.ui.app import STATIC_DIR

    api = await ui.get("/tenants")
    assert api.status_code == 200
    assert api.json() == []

    if not STATIC_DIR.is_dir():
        return

    # The shell itself carries no principal check, and that is right: it is a
    # bundle of JavaScript, not data. Everything it then asks for goes through
    # the same allow-list as any other call.
    transport = ASGITransport(app=ui_app)
    async with AsyncClient(transport=transport, base_url="http://ui") as client:
        page = await client.get("/")
    assert page.status_code == 200
    assert '<div id="root">' in page.text
