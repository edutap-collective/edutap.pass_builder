"""Rendering endpoints: create, update, save-link, preview. Scope `render`."""

from fastapi import APIRouter, Depends, Request, Response

from ..auth import AuthContext, require
from ..dependencies import get_render_service
from ..models.api import (
    CreatePassRequest,
    DeactivatePassRequest,
    DeactivatePassResponse,
    GooglePassResponse,
    PreviewRequest,
    PreviewResponse,
    SaveLinkRequest,
    UpdatePassRequest,
)
from ..models.enums import APPLE_WALLET_TYPES, Scope
from ..services.render import RenderResult, RenderService

router = APIRouter(tags=["passes"])

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
    if result.wallet_type in APPLE_WALLET_TYPES:
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
    if result.wallet_type in APPLE_WALLET_TYPES:
        return _apple_response(result)
    return Response(
        content=_google_response(result, pass_id).model_dump_json(),
        media_type="application/json",
        status_code=200,
    )


@router.post("/passes/{pass_id}/deactivate", response_model=DeactivatePassResponse)
async def deactivate_pass(
    pass_id: str,
    body: DeactivatePassRequest,
    request: Request,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    render_service: RenderService = Depends(get_render_service),  # noqa: B008
) -> DeactivatePassResponse:
    """Withdraw an issued pass. Google only; Apple answers 501.

    `POST` rather than `DELETE`, and the choice is not cosmetic: nothing is
    deleted here. This service keeps no register of issued passes -- there is
    no `pass` table -- so there is no row to remove. What happens is a state
    change on an object that lives at Google, and a `DELETE` would promise a
    removal that neither side performs.

    Idempotent: withdrawing an already withdrawn pass answers 200 again.
    """
    object_id, state = await render_service.deactivate_pass(
        auth,
        pass_id=pass_id,
        template_key=body.template,
        wallet_type=body.wallet_type,
        variant_key=body.variant,
        version_number=body.template_version,
        request_id=request.headers.get("x-request-id"),
    )
    return DeactivatePassResponse(pass_id=pass_id, object_id=object_id, state=state)


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


@router.get("/passes/{pass_type_identifier}/{serial_number}")
async def fetch_apple_pass(
    pass_type_identifier: str,
    serial_number: str,
    request: Request,
    auth: AuthContext = Depends(require(Scope.RENDER)),  # noqa: B008
    renderer: RenderService = Depends(get_render_service),  # noqa: B008
) -> Response:
    """Return the current `.pkpass` for an issued pass, by Apple's key alone.

    THE DELIVERY PATH for `wallet_apple_vas_web_service`, which holds
    registrations and knows no person, no template and no validity -- that
    ignorance is what makes it reusable at another institution, and it is why
    this route takes the only two values Apple gives it.

    A `GET`, and the only one on `/passes`: it has no effect beyond an audit
    entry, and a device may repeat it. The pass is *rebuilt* from the current
    template version and the person's current data rather than fetched from
    store -- this service holds no issued pass, and a stored copy would be a
    second truth with an unbounded staleness and a personal pass at rest.

    `410` for a withdrawn pass, distinct from the `404` of one that was never
    there: a device asking for a revoked pass should stop asking, and a `404`
    invites a retry.
    """
    result = await renderer.fetch_apple_pass(
        auth,
        pass_type_identifier=pass_type_identifier,
        serial_number=serial_number,
        request_id=request.headers.get("x-request-id"),
    )
    return _apple_response(result)
