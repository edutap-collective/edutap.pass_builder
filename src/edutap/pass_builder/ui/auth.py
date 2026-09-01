"""Who is allowed into the management UI, and how we know.

The render API authenticates an `api_client` by bearer token; this
authenticates a *person*, and the two never meet. A person has no row in
`api_client`, holds no scope, and is not a tenant's machine credential -- which
is also why the UI can create the first tenant and the first API client at all.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from pydantic import BaseModel

from ..errors import ProblemError
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
