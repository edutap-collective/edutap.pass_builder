"""Rendering endpoints: create, update, save-link, preview. Scope `render`."""

from fastapi import APIRouter, Depends, Request, Response

from ..auth import AuthContext, require
from ..dependencies import get_render_service
from ..models.api import (
    CreatePassRequest,
    GooglePassResponse,
    PreviewRequest,
    PreviewResponse,
    SaveLinkRequest,
    UpdatePassRequest,
)
from ..models.enums import Scope, WalletType
from ..services.render import RenderResult, RenderService

router = APIRouter(prefix="/api/v1", tags=["passes"])

_PKPASS_MEDIA_TYPE = "application/vnd.apple.pkpass"


def _apple_response(result: RenderResult) -> Response:
    """Build the raw `.pkpass` response with metadata carried as `X-` headers."""
    headers = {
        "X-Template-Version": str(result.template_version),
        "X-Variant": result.variant,
    }
    if result.credential_set is not None:
        headers["X-Credential-Set"] = result.credential_set
    return Response(
        content=result.pkpass, media_type=_PKPASS_MEDIA_TYPE, headers=headers
    )


def _google_response(result: RenderResult, pass_id: str) -> GooglePassResponse:
    """Build the JSON response describing a pushed Google Wallet object."""
    assert result.object_id is not None  # noqa: S101 - guaranteed by RenderService
    assert result.class_id is not None  # noqa: S101 - guaranteed by RenderService
    return GooglePassResponse(
        pass_id=pass_id,
        object_id=result.object_id,
        class_id=result.class_id,
        template_version=result.template_version,
        variant=result.variant,
    )


@router.post("/passes", status_code=200)
async def create_pass(
    body: CreatePassRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    render_service: RenderService = Depends(get_render_service),  # noqa: B008
) -> Response:
    """Create a pass. Apple returns signed bytes; Google returns `201` JSON."""
    result = await render_service.create_pass(
        auth,
        pass_id=body.pass_id,
        template_key=body.template,
        wallet_type=body.wallet_type,
        variant_key=body.variant,
        person_uid=body.person_uid,
        version_number=body.template_version,
        request_id=request.headers.get("x-request-id"),
    )
    if result.wallet_type == WalletType.APPLE:
        return _apple_response(result)
    return Response(
        content=_google_response(result, body.pass_id).model_dump_json(),
        media_type="application/json",
        status_code=201,
    )


@router.put("/passes/{pass_id}", status_code=200)
async def update_pass(
    pass_id: str,
    body: UpdatePassRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    render_service: RenderService = Depends(get_render_service),  # noqa: B008
) -> Response:
    """Re-render and re-deliver an existing pass. The variant may change here."""
    result = await render_service.update_pass(
        auth,
        pass_id=pass_id,
        template_key=body.template,
        wallet_type=body.wallet_type,
        variant_key=body.variant,
        person_uid=body.person_uid,
        version_number=body.template_version,
        request_id=request.headers.get("x-request-id"),
    )
    if result.wallet_type == WalletType.APPLE:
        return _apple_response(result)
    return Response(
        content=_google_response(result, pass_id).model_dump_json(),
        media_type="application/json",
        status_code=200,
    )


@router.post("/passes/{pass_id}/save-link")
async def save_link(
    pass_id: str,
    body: SaveLinkRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    render_service: RenderService = Depends(get_render_service),  # noqa: B008
) -> dict[str, str]:
    """Return a Google "save to wallet" link for an already-pushed object."""
    link = await render_service.save_link(
        auth,
        pass_id=pass_id,
        template_key=body.template,
        variant_key=body.variant,
        version_number=body.template_version,
        request_id=request.headers.get("x-request-id"),
    )
    return {"save_link": link}


@router.post("/passes/preview", response_model=PreviewResponse)
async def preview_pass(
    body: PreviewRequest,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    render_service: RenderService = Depends(get_render_service),  # noqa: B008
) -> PreviewResponse:
    """Dry-run render: substitute values, never sign or push. No audit entry."""
    result = await render_service.preview(
        auth,
        template_key=body.template,
        wallet_type=body.wallet_type,
        variant_key=body.variant,
        version_number=body.template_version,
        sample_data=body.sample_data or {},
    )
    return PreviewResponse(**result)
