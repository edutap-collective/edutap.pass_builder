"""How a management route names its permission, without owning the check.

The management routers are mounted three times -- consumer API, in-process
UI, admin -- and only the admin mount authenticates a person against the
permission map. `declares()` therefore names the permission ON the route (the
admin-ui design record wants it there: `templates` holds reads and writes
side by side) while the check itself lives with the application that mounted
the router: the admin app deposits its checker in `app.state`, every other
mount deposits nothing and the declaration is a no-op.
"""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from edutap.admin_auth import MalformedPermission, Permission
from fastapi import Request

PermissionCheck = Callable[[Request, str], Awaitable[Any]]
"""What the admin application deposits in ``app.state.admin_permission_check``."""


def declares(permission: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """Return a dependency naming this route's admin permission.

    ``permission`` is ``<object>:<verb>`` -- the tenant half comes from the
    route's path at check time. A malformed literal raises at import time,
    not as a refused request.
    """
    try:
        Permission.parse(f"{permission}@-")
    except MalformedPermission as error:
        raise MalformedPermission(
            f"declares() takes <object>:<verb> without a tenant; got {permission!r}"
        ) from error

    async def dependency(request: Request) -> None:
        check: PermissionCheck | None = getattr(
            request.app.state, "admin_permission_check", None
        )
        if check is not None:
            await check(request, permission)

    # For introspection and the completeness test: which permission a route
    # declared is readable off the dependency object itself.
    dependency.__declared_permission__ = permission  # ty: ignore[unresolved-attribute]
    return dependency
