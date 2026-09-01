"""Audit-on-failure guard for lifecycle router endpoints.

Mirrors `RenderService._render`'s try/except (see `services/render.py`):
every lifecycle action that already writes a `success` audit entry on
completion must also write a matching `outcome="error"` entry when it
fails, then re-raise -- per spec section 6, "An entry is written for every
call with an effect, including failures". Shared by `routers/credentials.py`
and `routers/templates.py` so the same try/except is not repeated six
times.
"""

from types import TracebackType
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext
from ..errors import ProblemError
from ..services.audit import elapsed_ms, write_audit_durable


class audited:
    """Async context manager wrapping one lifecycle action's effectful call.

    Wrap only the call(s) that perform the action itself. On a clean exit
    this writes nothing -- callers keep writing their own
    `outcome="success"` entry afterwards, since it commonly needs values
    only available once the wrapped call has returned (the created row, a
    freshly resolved variant, ...). On failure this writes the
    `outcome="error"` entry itself, durably (see
    `services/audit.py::write_audit_durable`) so it survives the request
    session's rollback in `database.get_session` once the exception below
    finishes propagating, and never swallows a failure of that write -- it
    is allowed to propagate, same as `_render`. A `ProblemError` keeps its
    `slug` as `error_code` and is re-raised unchanged; any other exception
    is reported as a generic
    `internal_error` (the original message is never audited or surfaced,
    it could carry secret material) and re-raised as
    `ProblemError(500, "internal_error", ...)`.

    No caller passes `subject_ref` or `requested_fields`: lifecycle
    actions have no person or field material to record, only the action,
    actor and tenant -- same as the routers' existing `_audit` helpers.
    """

    def __init__(
        self,
        session: AsyncSession,
        request: Request,
        auth: AuthContext,
        action: str,
        *,
        start: float,
        template_id: UUID | None = None,
        variant_id: UUID | None = None,
        version_id: UUID | None = None,
    ) -> None:
        """Store what the eventual error audit entry (if any) needs to write."""
        self._session = session
        self._request = request
        self._auth = auth
        self._action = action
        self._start = start
        self._template_id = template_id
        self._variant_id = variant_id
        self._version_id = version_id

    async def __aenter__(self) -> None:
        """Do nothing -- all the work happens in `__aexit__` on failure."""
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Write the error audit entry and decide how the exception continues.

        Returns `False` to let a `ProblemError` propagate unchanged, or
        raises a fresh `ProblemError(500, "internal_error", ...)` for
        anything else, chained via `from exc`.
        """
        if exc is None:
            return False
        error_code = exc.slug if isinstance(exc, ProblemError) else "internal_error"
        await write_audit_durable(
            self._session,
            tenant_id=self._auth.tenant_id,
            request_id=self._request.headers.get("x-request-id") or "",
            actor_client_id=self._auth.client_id,
            actor_principal=self._auth.principal,
            action=self._action,
            outcome="error",
            error_code=error_code,
            duration_ms=elapsed_ms(self._start),
            template_id=self._template_id,
            variant_id=self._variant_id,
            version_id=self._version_id,
            wallet_type=None,
            subject_ref=None,
            requested_fields=[],
        )
        if isinstance(exc, ProblemError):
            return False
        raise ProblemError(500, "internal_error", "Internal error") from exc
