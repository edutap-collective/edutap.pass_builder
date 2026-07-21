"""Write audit entries. Never records field values or secrets."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    request_id: str,
    actor_client_id: UUID,
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
