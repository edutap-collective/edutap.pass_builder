"""FastAPI dependency providers wiring settings, session and services.

Each provider is a request-scoped factory built from `get_settings()` and
the request-scoped database `session` (via `Depends(get_session)`). The
shared `httpx.AsyncClient` is read from `request.app.state.http` -- it is
created once at application startup rather than per request, so
`DataProviderClient` reuses one connection pool instead of opening a new
one on every call.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .clients.data_provider import DataProviderClient
from .clients.image_service import ImageServiceClient
from .clients.objectstore import ObjectStore
from .database import get_session
from .secrets.backend import SecretBackend
from .secrets.dbcrypto import DatabaseSecretBackend
from .services.credentials import CredentialService
from .services.render import RenderService
from .services.templates import TemplateService
from .settings import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_data_provider(request: Request, settings: SettingsDep) -> DataProviderClient:
    """Build a `DataProviderClient` from settings and the shared HTTP client."""
    return DataProviderClient(
        base_url=settings.data_provider_base_url,
        token=settings.data_provider_token.get_secret_value(),
        timeout=settings.data_provider_timeout,
        client=request.app.state.http,
    )


def get_image_service(request: Request, settings: SettingsDep) -> ImageServiceClient:
    """Build an `ImageServiceClient` from settings and the shared HTTP client."""
    return ImageServiceClient(
        base_url=settings.image_service_base_url,
        token=settings.image_service_token.get_secret_value(),
        timeout=settings.image_service_timeout,
        client=request.app.state.http,
    )


def get_objectstore(settings: SettingsDep) -> ObjectStore:
    """Build an `ObjectStore` from settings."""
    return ObjectStore(
        endpoint_url=settings.objectstore_endpoint_url,
        bucket=settings.objectstore_bucket,
        access_key=settings.objectstore_access_key,
        secret_key=settings.objectstore_secret_key.get_secret_value(),
    )


def get_secret_backend(settings: SettingsDep) -> SecretBackend:
    """Build the `SecretBackend` used to seal and open credential secrets."""
    return DatabaseSecretBackend(settings.secret_master_key.get_secret_value())


def get_template_service(
    session: SessionDep,
    objectstore: Annotated[ObjectStore, Depends(get_objectstore)],
) -> TemplateService:
    """Build a `TemplateService` bound to the request-scoped session."""
    return TemplateService(session, objectstore)


def get_credential_service(
    session: SessionDep,
    secret_backend: Annotated[SecretBackend, Depends(get_secret_backend)],
) -> CredentialService:
    """Build a `CredentialService` bound to the request-scoped session."""
    return CredentialService(session, secret_backend)


def get_render_service(
    session: SessionDep,
    templates: Annotated[TemplateService, Depends(get_template_service)],
    credentials: Annotated[CredentialService, Depends(get_credential_service)],
    data_provider: Annotated[DataProviderClient, Depends(get_data_provider)],
    images: Annotated[ImageServiceClient, Depends(get_image_service)],
    settings: SettingsDep,
) -> RenderService:
    """Build a `RenderService` bound to the session and its collaborators.

    `wwdr_certificate_path` is passed through explicitly so
    `Settings.wwdr_certificate_path` is what actually controls the Apple
    WWDR intermediate certificate used to sign -- see
    `RenderService._apple_signer`.
    """
    return RenderService(
        session,
        templates,
        credentials,
        data_provider,
        images=images,
        wwdr_certificate_path=settings.wwdr_certificate_path,
    )
