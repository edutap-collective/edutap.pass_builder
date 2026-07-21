"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the pass builder service."""

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_PASS_BUILDER_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str
    secret_master_key: str
    """Base64 encoded 32 byte AES key wrapping the per-secret data keys."""

    data_provider_base_url: str
    data_provider_token: str = ""
    data_provider_timeout: float = 10.0

    objectstore_endpoint_url: str = "http://localhost:9000"
    objectstore_bucket: str = "pass-builder"
    objectstore_access_key: str = ""
    objectstore_secret_key: str = ""

    wwdr_certificate_path: Path = Path("assets/wwdr-g4.pem")
    audit_retention_months: int = 24


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
