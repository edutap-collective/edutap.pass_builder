"""Audit log query endpoint. Scope `manage`."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require
from ..database import get_session
from ..models.api import AuditEntryResponse
from ..models.db import AuditLog
from ..models.enums import Scope

router = APIRouter(prefix="/api/v1", tags=["audit"])


def _to_response(entry: AuditLog) -> AuditEntryResponse:
    """Map an `AuditLog` row onto its response schema."""
    return AuditEntryResponse(
        id=entry.id,
        ts=entry.ts,
        request_id=entry.request_id,
        actor_client_id=entry.actor_client_id,
        action=entry.action,
        outcome=entry.outcome,
        error_code=entry.error_code,
        duration_ms=entry.duration_ms,
        template_id=entry.template_id,
        variant_id=entry.variant_id,
        version_id=entry.version_id,
        wallet_type=entry.wallet_type,
        subject_ref=entry.subject_ref,
        requested_fields=entry.requested_fields,
    )


@router.get("/audit", response_model=list[AuditEntryResponse])
async def list_audit(
    from_: datetime | None = None,
    to: datetime | None = None,
    template: UUID | None = None,
    subject_ref: str | None = None,
    outcome: str | None = None,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AuditEntryResponse]:
    """List the tenant's audit entries, optionally filtered.

    `template` filters by template id. `from_`/`to` bound `ts`. Results are
    ordered newest first.
    """
    query = select(AuditLog).where(
        AuditLog.tenant_id == auth.tenant_id  # ty: ignore[invalid-argument-type]
    )
    if from_ is not None:
        query = query.where(
            AuditLog.ts >= from_  # ty: ignore[invalid-argument-type]
        )
    if to is not None:
        query = query.where(
            AuditLog.ts <= to  # ty: ignore[invalid-argument-type]
        )
    if template is not None:
        query = query.where(
            AuditLog.template_id == template  # ty: ignore[invalid-argument-type]
        )
    if subject_ref is not None:
        query = query.where(
            AuditLog.subject_ref == subject_ref  # ty: ignore[invalid-argument-type]
        )
    if outcome is not None:
        query = query.where(
            AuditLog.outcome == outcome  # ty: ignore[invalid-argument-type]
        )
    query = query.order_by(
        AuditLog.ts.desc()  # ty: ignore[unresolved-attribute]
    )
    rows = (await session.execute(query)).scalars().all()
    return [_to_response(row) for row in rows]
