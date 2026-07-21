"""Credential set lifecycle endpoints. Scope `credentials`.

No endpoint here ever returns secret material -- see
`models.api.CredentialResponse`'s module docstring for the exact set of
forbidden fields.
"""

import json
import re
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require
from ..database import get_session
from ..dependencies import get_credential_service
from ..errors import ProblemError
from ..models.api import (
    CreateCredentialRequest,
    CredentialResponse,
    InstallCertificateRequest,
)
from ..models.db import CredentialSet
from ..models.enums import Provider, Scope
from ..services.audit import elapsed_ms, write_audit
from ..services.credentials import CredentialService

router = APIRouter(prefix="/api/v1", tags=["credentials"])

_EXPIRING_WITHIN_RE = re.compile(r"^(\d+)d$")


async def _audit(
    session: AsyncSession,
    request: Request,
    auth: AuthContext,
    action: str,
    *,
    start: float,
) -> None:
    """Write a `success` audit entry for a credential lifecycle action.

    Credential lifecycle events have no subject, template, variant or
    version to record, and never carry the secret material a credential set
    holds -- only the action, actor and tenant.
    """
    await write_audit(
        session,
        tenant_id=auth.tenant_id,
        request_id=request.headers.get("x-request-id") or "",
        actor_client_id=auth.client_id,
        action=action,
        outcome="success",
        error_code=None,
        duration_ms=elapsed_ms(start),
        template_id=None,
        variant_id=None,
        version_id=None,
        wallet_type=None,
        subject_ref=None,
        requested_fields=[],
    )


def _to_response(credential_set: CredentialSet) -> CredentialResponse:
    """Map a `CredentialSet` row onto its metadata-only response schema."""
    return CredentialResponse(
        id=credential_set.id,
        provider=credential_set.provider,
        label=credential_set.label,
        status=credential_set.status,
        pass_type_identifier=credential_set.pass_type_identifier,
        team_identifier=credential_set.team_identifier,
        organization_name=credential_set.organization_name,
        not_before=credential_set.not_before,
        not_after=credential_set.not_after,
        nfc_capable=credential_set.nfc_capable,
        service_account_email=credential_set.service_account_email,
        issuer_id=credential_set.issuer_id,
        cert_fingerprint_sha256=credential_set.cert_fingerprint_sha256,
    )


def _parse_expiring_within(raw: str | None) -> int | None:
    """Parse `expiring_within=90d` into a day count, or raise `invalid_request`."""
    if raw is None:
        return None
    match = _EXPIRING_WITHIN_RE.match(raw)
    if match is None:
        raise ProblemError(
            400, "invalid_request", "expiring_within must look like '90d'"
        )
    return int(match.group(1))


@router.get("/credentials", response_model=list[CredentialResponse])
async def list_credentials(
    provider: Provider | None = None,
    expiring_within: str | None = None,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
) -> list[CredentialResponse]:
    """List the tenant's credential sets, metadata only."""
    sets = await credentials.list_sets(
        auth.tenant_id,
        provider=provider,
        expiring_within_days=_parse_expiring_within(expiring_within),
    )
    return [_to_response(row) for row in sets]


@router.post("/credentials", status_code=201, response_model=CredentialResponse)
async def create_credential(
    body: CreateCredentialRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CredentialResponse:
    """Generate an Apple key/CSR, or import a Google service account.

    Apple requires `common_name`; Google requires `issuer_id` and
    `service_account_json`. Either missing is a `400 invalid_request`.
    """
    start = time.monotonic()
    if body.provider == Provider.APPLE:
        if not body.common_name:
            raise ProblemError(
                400, "invalid_request", "Apple credentials require common_name"
            )
        credential_set = await credentials.create_apple(
            auth.tenant_id, body.label, body.common_name
        )
    else:
        if not body.issuer_id or body.service_account_json is None:
            raise ProblemError(
                400,
                "invalid_request",
                "Google credentials require issuer_id and service_account_json",
            )
        raw = json.dumps(body.service_account_json).encode()
        credential_set = await credentials.import_google(
            auth.tenant_id, body.label, raw, issuer_id=body.issuer_id
        )
    await _audit(session, request, auth, "credential.create", start=start)
    return _to_response(credential_set)


@router.get("/credentials/{credential_id}/csr")
async def get_csr(
    credential_id: UUID,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
) -> Response:
    """Return the stored CSR in PEM. The CSR is public, never secret."""
    csr_pem = await credentials.get_csr(auth.tenant_id, credential_id)
    return Response(content=csr_pem, media_type="application/x-pem-file")


@router.put(
    "/credentials/{credential_id}/certificate", response_model=CredentialResponse
)
async def install_certificate(
    credential_id: UUID,
    body: InstallCertificateRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CredentialResponse:
    """Install the signed certificate, activating a pending Apple credential set."""
    start = time.monotonic()
    credential_set = await credentials.install_certificate(
        auth.tenant_id, credential_id, body.certificate_pem.encode()
    )
    await _audit(
        session, request, auth, "credential.certificate_installed", start=start
    )
    return _to_response(credential_set)


@router.post("/credentials/{credential_id}/renew", response_model=CredentialResponse)
async def renew_credential(
    credential_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CredentialResponse:
    """Create a successor Apple credential set with a fresh keypair and CSR."""
    start = time.monotonic()
    successor = await credentials.renew(auth.tenant_id, credential_id)
    await _audit(session, request, auth, "credential.renew", start=start)
    return _to_response(successor)


@router.delete("/credentials/{credential_id}", status_code=204)
async def revoke_credential(
    credential_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require(Scope.CREDENTIALS)),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Mark a credential set `revoked`. Never a hard delete."""
    start = time.monotonic()
    await credentials.revoke(auth.tenant_id, credential_id)
    await _audit(session, request, auth, "credential.revoke", start=start)
