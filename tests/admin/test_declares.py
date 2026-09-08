"""declares(): the permission a route names, checked only where admin context exists."""

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from edutap.pass_builder.admin.permissions import declares


def app_with_route() -> FastAPI:
    app = FastAPI()

    @app.get(
        "/tenants/{tenant_id}/things",
        dependencies=[Depends(declares("templates:read"))],
    )
    async def things(tenant_id: str) -> dict:
        return {"ok": True}

    return app


def test_without_an_admin_context_the_declaration_is_a_no_op():
    # The same routers are mounted in the consumer API and the in-process UI,
    # where current_auth / the principal decide. The declaration must not
    # interfere there.
    client = TestClient(app_with_route())
    assert client.get("/tenants/x/things").status_code == 200


def test_with_an_admin_context_the_declared_permission_is_enforced():
    app = app_with_route()
    checked: list[tuple[str, str]] = []

    async def check(request: Request, permission: str):
        checked.append((request.path_params["tenant_id"], permission))
        raise HTTPException(403, "nope")

    app.state.admin_permission_check = check
    response = TestClient(app).get("/tenants/lmu-ub/things")
    assert response.status_code == 403
    assert checked == [("lmu-ub", "templates:read")]


def test_a_malformed_permission_literal_fails_at_import_time():
    with pytest.raises(Exception, match="templates"):
        declares("templates")
