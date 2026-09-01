"""Write audit entries. Never records field values or secrets."""

import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.db import AuditLog


def elapsed_ms(start: float) -> int:
    """Return the milliseconds elapsed since `start` (a `time.monotonic()`).

    Shared by every caller that measures a lifecycle action's duration for
    its audit entry -- `RenderService` keeps its own copy for the hot render
    path, but routers writing the simpler lifecycle events (credential,
    template, variant) use this one.
    """
    return int((time.monotonic() - start) * 1000)


async def write_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    request_id: str,
    actor_client_id: UUID | None,
    actor_principal: str | None = None,
    action: str,
    outcome: str,
    error_code: str | None,
    duration_ms: int,
    template_id: UUID | None,
    variant_id: UUID | None,
    version_id: UUID | None,
    wallet_type: str | None,
    subject_ref: str | None,
    requested_fields: list[str],
) -> None:
    """Persist one audit entry within the caller's transaction.

    Only field *names* (`requested_fields`) and the person identifier
    (`subject_ref`) are stored -- never a field value or secret material.
    """
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            ts=datetime.now(UTC),
            request_id=request_id,
            actor_client_id=actor_client_id,
            actor_principal=actor_principal,
            action=action,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=subject_ref,
            requested_fields=requested_fields,
            details={},
        )
    )


async def write_audit_durable(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    request_id: str,
    actor_client_id: UUID | None,
    actor_principal: str | None = None,
    action: str,
    outcome: str,
    error_code: str | None,
    duration_ms: int,
    template_id: UUID | None,
    variant_id: UUID | None,
    version_id: UUID | None,
    wallet_type: str | None,
    subject_ref: str | None,
    requested_fields: list[str],
) -> None:
    """Persist one audit entry durably, on its own transaction.

    Used for `outcome="error"` entries written from an exception handler
    (`services/render.py::RenderService._write_error_audit`,
    `routers/_lifecycle_audit.py::audited`): the request's own session is
    about to be rolled back by `database.get_session` once the exception it
    is handling propagates out, which would silently discard an entry
    written through the plain `write_audit` above along with the rest of
    the failed request. Opening a fresh session on the same bind as
    `session` -- the process engine in production, so a genuinely separate
    connection and transaction; the same already-open connection in tests
    bound to one, where an external transaction owns the eventual rollback
    -- and committing it here, before returning, means the entry survives
    that rollback either way, without ever sharing `session`'s own pending,
    about-to-be-discarded state.
    """
    maker = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    async with maker() as audit_session:
        await write_audit(
            audit_session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_client_id=actor_client_id,
            actor_principal=actor_principal,
            action=action,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=subject_ref,
            requested_fields=requested_fields,
        )
        await audit_session.commit()
