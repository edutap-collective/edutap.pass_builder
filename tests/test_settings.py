import pytest
from pydantic import ValidationError

from edutap.pass_builder.settings import Settings


def test_settings_read_prefixed_environment(monkeypatch):
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://x/y"
    assert settings.audit_retention_months == 24
    assert settings.data_provider_timeout == 10.0


@pytest.fixture
def required_env(monkeypatch):
    """The three settings without a default. Every Settings() needs them."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")


def test_base_path_defaults_to_the_most_restrictive_zone(required_env):
    """A service whose zone nobody configured must not be publicly mounted."""
    assert Settings().base_path == "/internal-api/wallet"


def test_base_path_follows_class_and_domain(required_env, monkeypatch):
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_CLASS", "public-api")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_DOMAIN", "vzd")
    assert Settings().base_path == "/public-api/vzd"


def test_base_path_carries_the_staging_suffix(required_env, monkeypatch):
    """The webfe schema appends "-test" to the zone, not to the service name."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_CLASS", "api")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_SUFFIX", "-test")
    assert Settings().base_path == "/api-test/wallet"


def test_unknown_api_class_is_rejected(required_env, monkeypatch):
    """A typo must fail at startup, not mount the service somewhere unintended."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_API_CLASS", "publicapi")
    with pytest.raises(ValidationError):
        Settings()


def test_public_origin_is_optional(required_env):
    """Only services building absolute URLs (Apple, Google) need it."""
    assert Settings().public_origin is None
