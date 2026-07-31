"""Application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the pass builder service."""

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_PASS_BUILDER_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str
    secret_master_key: SecretStr
    """Base64 encoded 32 byte AES key wrapping the per-secret data keys."""

    data_provider_base_url: str
    data_provider_token: SecretStr = SecretStr("")
    data_provider_timeout: float = 10.0

    objectstore_endpoint_url: str = "http://localhost:9000"
    objectstore_bucket: str = "pass-builder"
    objectstore_access_key: str = ""
    objectstore_secret_key: SecretStr = SecretStr("")

    wwdr_certificate_path: Path = Path("assets/wwdr-g4.pem")
    audit_retention_months: int = 24

    # ---- Mount point ----
    #
    # Deployment concern, not part of the API contract: the service's own path
    # ("/builder/v1") lives in app.py as API_PREFIX. Moving this service between
    # zones is a matter of environment variables plus a Traefik label -- no code
    # change. See the design spec 2026-07-31-api-basispfad-konvention-design.md
    # in lmu_edutap_dev_setup.

    api_class: Literal["api", "public-api", "internal-api"] = "internal-api"
    """Zone the webfe enforces in front of this service.

    "api" means Shibboleth, "public-api" means the service authenticates
    callers itself, "internal-api" means no webfe entry at all. The default is
    the most restrictive one on purpose: a service whose zone nobody configured
    must end up unreachable, not accidentally exposed.
    """

    api_suffix: str = ""
    """Staging postfix of the webfe path schema: "-test" yields /api-test/…"""

    api_domain: str = "wallet"
    """Business domain: "wallet" or "vzd"."""

    public_origin: HttpUrl | None = None
    """External origin, e.g. https://portal-mgmt.verwaltung.uni-muenchen.de.

    Only needed where absolute URLs are generated -- Apple's `webServiceURL`
    and Google's image URLs are baked into issued passes. The pass builder does
    not generate any, so it stays unset here.
    """

    @property
    def base_path(self) -> str:
        """Mount point of this service, fed into FastAPI's root_path."""
        return f"/{self.api_class}{self.api_suffix}/{self.api_domain}"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
