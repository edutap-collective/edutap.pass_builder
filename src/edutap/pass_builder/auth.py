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
    """The authenticated caller."""

    client_id: UUID
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


def require(*scopes: Scope) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Return a dependency enforcing the given scopes."""

    async def dependency(
        request: Request,
        session: AsyncSession = Depends(get_session),  # noqa: B008
    ) -> AuthContext:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise ProblemError(401, "unauthenticated", "Missing bearer token")
        context = await resolve_token(session, header.removeprefix("Bearer "))
        if not set(scopes).issubset(context.scopes):
            raise ProblemError(403, "insufficient_scope", "Missing required scope")
        return context

    return dependency
