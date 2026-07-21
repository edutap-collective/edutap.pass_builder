"""Liveness and readiness endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report that the process is alive."""
    return {"status": "ok"}
