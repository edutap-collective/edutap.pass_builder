"""Every management route names its admin permission.

Checked on the route objects, not the source: a new route without a
declaration must turn this red, because in the admin mount an undeclared
route would be reachable with authentication alone.
"""

from fastapi.routing import APIRoute

from edutap.pass_builder.routers import audit, credentials, fields, templates
from edutap.pass_builder.ui.routers import tenants


def declared_permissions(route: APIRoute) -> list[str]:
    found = []
    for dependency in route.dependencies:
        call = dependency.dependency
        if getattr(call, "__qualname__", "").startswith("declares."):
            found.append(getattr(call, "__declared_permission__"))  # noqa: B009
    return found


def routes_of(router) -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


def test_every_management_route_declares_exactly_one_permission():
    undeclared = []
    for router in (templates.router, credentials.router, fields.router, audit.router):
        for route in routes_of(router):
            if len(declared_permissions(route)) != 1:
                undeclared.append(f"{sorted(route.methods or ())} {route.path}")
    assert undeclared == []


def test_the_ui_client_routes_declare_client_permissions():
    by_route = {
        f"{sorted(r.methods or ())[0]} {r.path}": declared_permissions(r)
        for r in routes_of(tenants.router)
    }
    assert by_route["GET /tenants/{tenant_id}/clients"] == ["clients:read"]
    assert by_route["POST /tenants/{tenant_id}/clients"] == ["clients:write"]
    assert by_route["POST /tenants/{tenant_id}/clients/{client_id}/revoke"] == [
        "clients:write"
    ]
    # The tenant list has no tenant segment to check against; it stays behind
    # authentication alone, which is what the admin-ui design record asks for
    # ("the interface therefore shows tenants").
    assert by_route["GET /tenants"] == []


def test_reads_and_writes_are_told_apart():
    by_route = {
        f"{sorted(r.methods or ())[0]} {r.path}": declared_permissions(r)
        for r in routes_of(templates.router)
    }
    assert by_route["GET /templates"] == ["templates:read"]
    assert by_route["POST /templates"] == ["templates:write"]


def test_fields_refresh_is_its_own_verb():
    by_route = {
        f"{sorted(r.methods or ())[0]} {r.path}": declared_permissions(r)
        for r in routes_of(fields.router)
    }
    assert by_route["POST /fields/refresh"] == ["fields:refresh"]
    assert by_route["GET /fields"] == ["fields:read"]
