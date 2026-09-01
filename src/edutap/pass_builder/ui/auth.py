"""Who is allowed into the management UI, and how we know.

The render API authenticates an `api_client` by bearer token; this
authenticates a *person*, and the two never meet. A person has no row in
`api_client`, holds no scope, and is not a tenant's machine credential -- which
is also why the UI can create the first tenant and the first API client at all.
"""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext
from ..database import get_session
from ..errors import ProblemError
from ..models.db import Tenant
from ..models.enums import Scope
from ..settings import Settings, get_settings

GROUP_SEPARATOR = ";"
"""How the web frontend joins multi-valued attributes.

Shibboleth's default for `isMemberOf`. A group containing a semicolon would be
indistinguishable from two groups -- that is the frontend's encoding to fix,
not ours to guess around.
"""


class Principal(BaseModel):
    """The authenticated person in front of the UI."""

    name: str
    groups: frozenset[str]


def _principal_from(request: Request, settings: Settings) -> Principal:
    """Read the principal the web frontend asserted, or refuse."""
    name = request.headers.get(settings.ui_remote_user_header, "").strip()
    if not name:
        # 401 and not 403: nobody has been identified yet. Reaching this means
        # the request did not pass through the web frontend -- either the zone
        # is misconfigured or something is talking to the container directly.
        raise ProblemError(
            401, "unauthenticated", "No authenticated principal was asserted"
        )
    raw_groups = request.headers.get(settings.ui_groups_header, "")
    groups = frozenset(
        part.strip() for part in raw_groups.split(GROUP_SEPARATOR) if part.strip()
    )
    return Principal(name=name, groups=groups)


def is_authorised(principal: Principal, settings: Settings) -> bool:
    """Whether this person may use the UI.

    By name or by group, and either is enough -- a deployment starts with one
    named person and moves to a group without a code change.

    **Two empty lists deny everyone.** That is the same reasoning as the
    default zone: an installation nobody has configured must end up
    unreachable rather than open. The failure mode of the opposite default is
    an administration interface for signing credentials, standing open, with
    nothing about the deployment looking wrong.
    """
    users = settings.ui_authorised_user_set
    groups = settings.ui_authorised_group_set
    if not users and not groups:
        return False
    return principal.name in users or bool(principal.groups & groups)


def require_principal() -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Return the dependency that authenticates and authorises a person."""

    async def dependency(
        request: Request,
        settings: Settings = Depends(get_settings),  # noqa: B008
    ) -> Principal:
        principal = _principal_from(request, settings)
        if not is_authorised(principal, settings):
            raise ProblemError(
                403, "not_authorised", "This principal may not use the management UI"
            )
        return principal

    return dependency


async def ui_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AuthContext:
    """Build the caller for a management router mounted under a tenant path.

    THIS IS WHAT LETS THE UI REUSE THE ROUTERS. `app.py` mounts the management
    routers behind `current_auth`, which resolves a bearer token; the UI mounts
    the same routers and overrides that dependency with this one. A second
    implementation of publishing or of a credential upload would be a second
    set of rules about immutability and key wrapping, and it is the copy that
    stops being maintained.

    Two things differ from the token path, and both are deliberate:

    * **The tenant comes from the path, not from the caller.** A token belongs
      to exactly one tenant; a person does not, so the UI says which one it is
      working in and the tenant is checked to exist before any router body
      runs. Every service call below is still tenant-scoped by that value, so
      the scoping that protects one tenant from another is unchanged.
    * **Every scope is granted.** They exist to limit what one machine
      credential may do; a person who is allow-listed for this UI at all is
      allow-listed for what it offers. The UI mounts only the management
      routers -- rendering a pass is not among them.
    """
    principal = _principal_from(request, settings)
    if not is_authorised(principal, settings):
        raise ProblemError(
            403, "not_authorised", "This principal may not use the management UI"
        )
    raw = request.path_params.get("tenant_id")
    if raw is None:  # pragma: no cover - every mounted path carries the segment
        raise ProblemError(500, "tenant_missing", "Route carries no tenant segment")
    try:
        tenant_id = UUID(str(raw))
    except ValueError as exc:
        raise ProblemError(404, "tenant_not_found", "Tenant not found") from exc
    exists = (
        await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)  # ty: ignore[invalid-argument-type]
        )
    ).scalar_one_or_none()
    if exists is None:
        raise ProblemError(404, "tenant_not_found", "Tenant not found")
    return AuthContext(principal=principal.name, tenant_id=tenant_id, scopes=set(Scope))


async def tenant_path_parameter(tenant_id: UUID) -> UUID:
    """Declare `{tenant_id}` so it exists in the OpenAPI document.

    A router mounted under a path template does not by itself put that
    template's parameter into the schema -- FastAPI documents the parameters
    an operation *declares*, and none of the reused management endpoints
    declares this one. Without this dependency the document describes routes
    whose `{tenant_id}` no generated client can fill, which is exactly the
    kind of contract error that is invisible until someone generates a client.

    `ui_auth_context` reads the same segment through `request.path_params`
    rather than through this value: it must resolve the caller even if the
    dependency order ever changes, and a check that can be reordered away is
    not a check.
    """
    return tenant_id
