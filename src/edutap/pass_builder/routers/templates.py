"""Template, variant, version, mapping and asset endpoints. Scope `manage`."""

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from ..auth import AuthContext, require
from ..dependencies import get_credential_service, get_template_service
from ..errors import ProblemError
from ..models.api import (
    CreateGoogleVersionRequest,
    CreateTemplateRequest,
    CreateVariantRequest,
    MappingRulesRequest,
    MappingRulesResponse,
    TemplateResponse,
    UpdateTemplateRequest,
    UpdateVariantRequest,
    ValidationResponse,
    VariantResponse,
    VersionResponse,
)
from ..models.db import Template, TemplateAsset, TemplateVariant, TemplateVersion
from ..models.enums import Scope
from ..services.credentials import CredentialService
from ..services.templates import TemplateService

router = APIRouter(prefix="/api/v1", tags=["templates"])


def _template_response(template: Template) -> TemplateResponse:
    """Map a `Template` row onto its response schema."""
    return TemplateResponse(
        id=template.id,
        key=template.key,
        name=template.name,
        description=template.description,
        created_at=template.created_at,
        archived_at=template.archived_at,
    )


def _variant_response(variant: TemplateVariant) -> VariantResponse:
    """Map a `TemplateVariant` row onto its response schema."""
    return VariantResponse(
        id=variant.id,
        template_id=variant.template_id,
        key=variant.key,
        name=variant.name,
        wallet_type=variant.wallet_type,
        is_default=variant.is_default,
        credential_set_id=variant.credential_set_id,
        google_class_id=variant.google_class_id,
        created_at=variant.created_at,
        archived_at=variant.archived_at,
    )


def _version_response(version: TemplateVersion) -> VersionResponse:
    """Map a `TemplateVersion` row onto its response schema."""
    return VersionResponse(
        id=version.id,
        variant_id=version.variant_id,
        number=version.number,
        status=version.status,
        nfc_enabled=version.nfc_enabled,
        nfc_encryption_public_key=version.nfc_encryption_public_key,
        nfc_requires_authentication=version.nfc_requires_authentication,
        notes=version.notes,
        created_at=version.created_at,
        published_at=version.published_at,
    )


# --- templates ---------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> list[TemplateResponse]:
    """List every template of the tenant."""
    rows = await templates.list_templates(auth.tenant_id)
    return [_template_response(row) for row in rows]


@router.post("/templates", status_code=201, response_model=TemplateResponse)
async def create_template(
    body: CreateTemplateRequest,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> TemplateResponse:
    """Create a new template."""
    template = await templates.create_template(
        auth.tenant_id, body.key, body.name, body.description
    )
    return _template_response(template)


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> TemplateResponse:
    """Return one template."""
    template = await templates.get_template(auth.tenant_id, template_id)
    return _template_response(template)


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: UUID,
    body: UpdateTemplateRequest,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> TemplateResponse:
    """Patch a template's name or description."""
    template = await templates.update_template(
        auth.tenant_id, template_id, body.name, body.description
    )
    return _template_response(template)


@router.delete("/templates/{template_id}", response_model=TemplateResponse)
async def archive_template(
    template_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> TemplateResponse:
    """Archive a template. Never a hard delete."""
    template = await templates.archive_template(auth.tenant_id, template_id)
    return _template_response(template)


# --- variants ------------------------------------------------------------------


@router.get("/templates/{template_id}/variants", response_model=list[VariantResponse])
async def list_variants(
    template_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> list[VariantResponse]:
    """List every variant of a template."""
    rows = await templates.list_variants(auth.tenant_id, template_id)
    return [_variant_response(row) for row in rows]


@router.post(
    "/templates/{template_id}/variants", status_code=201, response_model=VariantResponse
)
async def create_variant(
    template_id: UUID,
    body: CreateVariantRequest,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VariantResponse:
    """Create a new variant under a template."""
    variant = await templates.create_variant(
        auth.tenant_id,
        template_id,
        key=body.key,
        name=body.name,
        wallet_type=body.wallet_type,
        is_default=body.is_default,
        credential_set_id=body.credential_set_id,
        google_class_id=body.google_class_id,
    )
    return _variant_response(variant)


@router.get("/variants/{variant_id}", response_model=VariantResponse)
async def get_variant(
    variant_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VariantResponse:
    """Return one variant."""
    variant = await templates.get_variant(auth.tenant_id, variant_id)
    return _variant_response(variant)


@router.patch("/variants/{variant_id}", response_model=VariantResponse)
async def update_variant(
    variant_id: UUID,
    body: UpdateVariantRequest,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VariantResponse:
    """Patch a variant, including `is_default` and `credential_set_id`."""
    variant = await templates.update_variant(
        auth.tenant_id,
        variant_id,
        name=body.name,
        is_default=body.is_default,
        credential_set_id=body.credential_set_id,
        google_class_id=body.google_class_id,
    )
    return _variant_response(variant)


@router.post("/variants/{variant_id}/sync")
async def sync_variant(
    variant_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
    credentials: CredentialService = Depends(get_credential_service),  # noqa: B008
) -> dict[str, str]:
    """Push a Google variant's published class definition. Idempotent."""
    variant = await templates.get_variant(auth.tenant_id, variant_id)
    if variant.credential_set_id is None:
        raise ProblemError(
            409,
            "google_credentials_missing",
            "No Google credential set configured for this variant",
        )
    credential_set = await credentials.get(auth.tenant_id, variant.credential_set_id)
    material = await credentials.open_material(credential_set)
    google_credentials: dict[str, Any] = json.loads(material)
    await templates.sync_variant(auth.tenant_id, variant_id, google_credentials)
    return {"status": "synced"}


# --- versions --------------------------------------------------------------------


@router.get("/variants/{variant_id}/versions", response_model=list[VersionResponse])
async def list_versions(
    variant_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> list[VersionResponse]:
    """List every version of a variant."""
    rows = await templates.list_versions(auth.tenant_id, variant_id)
    return [_version_response(row) for row in rows]


@router.post(
    "/variants/{variant_id}/versions", status_code=201, response_model=VersionResponse
)
async def create_version(
    variant_id: UUID,
    request: Request,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VersionResponse:
    """Create a draft version.

    Apple: `multipart/form-data` with the `.pkpasstemplate` bundle as
    `file`. Google: a JSON body with `class_json` and `object_json`. The
    content type decides which path runs, since the two platforms need
    structurally different payloads.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ProblemError(400, "invalid_request", "Expected a 'file' upload field")
        bundle = await upload.read()
        version = await templates.import_apple_version(
            auth.tenant_id, variant_id, bundle
        )
        return _version_response(version)

    payload = CreateGoogleVersionRequest(**(await request.json()))
    version = await templates.create_google_version(
        auth.tenant_id, variant_id, payload.class_json, payload.object_json
    )
    return _version_response(version)


@router.get("/versions/{version_id}", response_model=VersionResponse)
async def get_version(
    version_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VersionResponse:
    """Return one version."""
    version = await templates.get_version(auth.tenant_id, version_id)
    return _version_response(version)


@router.get("/versions/{version_id}/mappings", response_model=MappingRulesResponse)
async def get_mappings(
    version_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> MappingRulesResponse:
    """Return a version's mapping rules."""
    rules = await templates.get_mappings(auth.tenant_id, version_id)
    return MappingRulesResponse(rules=rules)


@router.put("/versions/{version_id}/mappings", response_model=MappingRulesResponse)
async def set_mappings(
    version_id: UUID,
    body: MappingRulesRequest,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> MappingRulesResponse:
    """Bulk replace a draft version's mapping rules. `409` once published."""
    await templates.set_mappings(auth.tenant_id, version_id, body.rules)
    rules = await templates.get_mappings(auth.tenant_id, version_id)
    return MappingRulesResponse(rules=rules)


@router.post("/versions/{version_id}/validate", response_model=ValidationResponse)
async def validate_version(
    version_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> ValidationResponse:
    """Run full publish-time validation without publishing."""
    findings = await templates.validate_version(auth.tenant_id, version_id)
    return ValidationResponse(valid=not findings, findings=findings)


@router.post("/versions/{version_id}/publish", response_model=VersionResponse)
async def publish_version(
    version_id: UUID,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> VersionResponse:
    """Validate then publish a draft version, archiving its predecessor."""
    version = await templates.publish(auth.tenant_id, version_id)
    return _version_response(version)


# --- assets ----------------------------------------------------------------------


def _asset_media_type(asset: TemplateAsset) -> str:
    """Return an asset's stored media type, falling back to a safe default."""
    return asset.media_type or "application/octet-stream"


@router.get("/versions/{version_id}/assets/{filename}")
async def get_asset(
    version_id: UUID,
    filename: str,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> Response:
    """Return one version's asset bytes."""
    asset, data = await templates.get_asset(auth.tenant_id, version_id, filename)
    return Response(content=data, media_type=_asset_media_type(asset))


@router.put("/versions/{version_id}/assets/{filename}")
async def put_asset(
    version_id: UUID,
    filename: str,
    request: Request,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> dict[str, Any]:
    """Replace one draft version's asset. `409` once the version is published."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ProblemError(400, "invalid_request", "Expected a 'file' upload field")
        data = await upload.read()
        media_type = upload.content_type
    else:
        data = await request.body()
        media_type = content_type or None
    asset = await templates.put_asset(
        auth.tenant_id, version_id, filename, data, media_type
    )
    return {
        "filename": asset.filename,
        "media_type": asset.media_type,
        "size": asset.size,
        "sha256": asset.sha256,
    }


@router.delete("/versions/{version_id}/assets/{filename}", status_code=204)
async def delete_asset(
    version_id: UUID,
    filename: str,
    auth: AuthContext = Depends(require(Scope.MANAGE)),  # noqa: B008
    templates: TemplateService = Depends(get_template_service),  # noqa: B008
) -> None:
    """Remove one draft version's asset. `409` once the version is published."""
    await templates.delete_asset(auth.tenant_id, version_id, filename)
