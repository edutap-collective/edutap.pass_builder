"""API client authentication and scope enforcement."""

import hashlib
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .errors import ProblemError
from .models.db import ApiClient
from .models.enums import Scope


class AuthContext(BaseModel):
    """The authenticated caller, machine or person.

    Two kinds of caller reach the same management operations: an `api_client`
    holding a bearer token, and a person in front of the management UI. They
    are told apart by which of the two actor fields is set, and the audit log
    keeps that distinction -- see `AuditLog.actor_principal`.
    """

    client_id: UUID | None = None
    """The `api_client` row, where a machine credential authenticated."""

    principal: str | None = None
    """The person the web frontend asserted, where one did."""

    tenant_id: UUID
    scopes: set[Scope]


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of a bearer token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def resolve_token(session: AsyncSession, token: str) -> AuthContext:
    """Resolve a bearer token to its auth context."""
    row = (
        await session.execute(
            select(ApiClient).where(
                # ty infers SQLModel columns as their plain Python type (str,
                # bool) rather than InstrumentedAttribute, so these read as
                # comparisons on plain values instead of SQL expressions.
                ApiClient.token_hash  # ty: ignore[invalid-argument-type]
                == hash_token(token),
                ApiClient.active.is_(True),  # ty: ignore[unresolved-attribute]
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProblemError(401, "unauthenticated", "Unknown or inactive token")
    return AuthContext(
        client_id=row.id, tenant_id=row.tenant_id, scopes=set(row.scopes)
    )


async def current_auth(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AuthContext:
    """Return the caller, authenticated by bearer token.

    THE ONE PLACE A CALLER IS ESTABLISHED, and the seam the management UI
    replaces: it mounts these same routers and overrides this dependency with
    one that reads a person from the web frontend instead. Establishing the
    caller inside `require` -- where it used to live -- made that impossible,
    because every `require(...)` call produces a distinct function object and
    `dependency_overrides` keys on identity.

    That the UI can reuse the routers rather than restate them is the point:
    a second implementation of publishing or of a credential upload is a
    second set of rules about immutability and key wrapping, and it is the
    copy that stops being maintained.
    """
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise ProblemError(401, "unauthenticated", "Missing bearer token")
    return await resolve_token(session, header.removeprefix("Bearer "))


def require(*scopes: Scope) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Return a dependency enforcing the given scopes."""

    async def dependency(
        auth: AuthContext = Depends(current_auth),  # noqa: B008
    ) -> AuthContext:
        if not set(scopes).issubset(auth.scopes):
            raise ProblemError(403, "insufficient_scope", "Missing required scope")
        return auth

    return dependency
