"""RFC 9457 problem responses with stable machine readable slugs."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_TYPE_PREFIX = "urn:edutap:pass-builder:"


class ProblemError(Exception):
    """An error that is rendered as an application/problem+json response."""

    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        **extra: Any,
    ) -> None:
        """Store the RFC 9457 fields that make up the problem document."""
        super().__init__(title)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        """Return the problem document body."""
        body: dict[str, Any] = {
            "type": f"{PROBLEM_TYPE_PREFIX}{self.slug}",
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handler on the application."""

    @app.exception_handler(ProblemError)
    async def _handle(_: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content=error.to_dict(),
            media_type="application/problem+json",
        )
