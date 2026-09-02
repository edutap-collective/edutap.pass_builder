import pytest
from edutap.db_definitions.settings import ClusterSettings
from pydantic import ValidationError

from edutap.pass_builder.settings import SECRETS_DIR, DatabaseSettings, Settings


@pytest.fixture
def required_env(monkeypatch):
    """The two settings without a default. Every Settings() needs them."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")


@pytest.fixture
def required_db_env(monkeypatch):
    """The four database fields, none of which carries a default."""
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_HOSTS", "pg-a,pg-b:5433")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_DATABASE", "edutap")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_USER", "pass_builder")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_PASSWORD", "secret")


def test_settings_read_prefixed_environment(required_env):
    settings = Settings()
    assert settings.audit_retention_months == 24
    assert settings.data_provider_timeout == 10.0


def test_settings_no_longer_carry_a_dsn(required_env):
    """A full DSN would put the database password back into the environment.

    It lived here until 2026-09-01 as `database_url`. Reaching the cluster is
    `DatabaseSettings` now -- every node, the primary among them, and TLS.
    """
    assert not hasattr(Settings(), "database_url")


def test_database_settings_build_a_multihost_url(required_db_env):
    """Every node reaches the driver, not just the first.

    The hosts arrive as repeated `host=` query parameters, each with an
    explicit port -- the only form SQLAlchemy hands to asyncpg as a *list*.
    The colon is percent-encoded because it is a query value, and a host
    without a port makes the dialect raise rather than guess.
    """
    url = DatabaseSettings().async_url
    assert "host=pg-a%3A5432" in url
    assert "host=pg-b%3A5433" in url
    assert "target_session_attrs=read-write" in url


def test_database_settings_derive_from_the_shared_cluster_class(required_db_env):
    """The connection logic is shared, not copied.

    A second implementation here would be a second truth about how this
    cluster is reached, and it is the copy that stops being maintained.
    """
    assert isinstance(DatabaseSettings(), ClusterSettings)


@pytest.mark.parametrize(
    "missing",
    ["HOSTS", "DATABASE", "USER", "PASSWORD"],
)
def test_database_settings_have_no_defaults(required_db_env, monkeypatch, missing):
    """A misspelled prefix must abort, not start against *some* database.

    `ClusterSettings` gives these development defaults on purpose. Inheriting
    them here would turn a typo in the deployment into a service that comes up
    clean and writes somewhere nobody looks.
    """
    monkeypatch.delenv(f"EDUTAP_PASS_BUILDER_DB_{missing}")
    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_both_settings_classes_declare_a_secrets_dir():
    """The master key, the database password and the S3 key arrive as files.

    This asserts the *declaration*, not the behaviour, and that is the point:
    without a `secrets_dir` there is no file that is read wrongly -- only one
    that is never read. Nothing observable separates that from a deployment
    without the secret, and it is exactly this silence that carried the same
    fault into two other services on 2026-08-27.

    pydantic-settings has no `_FILE` convention, and the file name it looks for
    carries the prefix: `/run/secrets/EDUTAP_PASS_BUILDER_secret_master_key`.
    """
    assert Settings.model_config["secrets_dir"] == SECRETS_DIR
    assert DatabaseSettings.model_config["secrets_dir"] == SECRETS_DIR


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
