from edutap.pass_builder.settings import Settings


def test_settings_read_prefixed_environment(monkeypatch):
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://x/y"
    assert settings.audit_retention_months == 24
    assert settings.data_provider_timeout == 10.0
