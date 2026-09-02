"""Der Multihost-DSN überlebt den Weg durch Alembics ConfigParser."""

from alembic.config import Config

from edutap.pass_builder.settings import DatabaseSettings


def test_a_multihost_url_survives_alembics_config(monkeypatch):
    """`set_main_option` schreibt in einen ConfigParser, der `%` deutet.

    Die Knotenliste kommt als wiederholte `host=<name>%3A<port>`-Parameter, und
    jedes `%3A` sieht für den Parser wie ein kaputtes `%(name)s` aus. Vor dem
    2026-09-02 brach der Migrationslauf daran ab, bevor er die Datenbank auch
    nur angefasst hatte -- und zwar erst auf dem Cluster, weil eine
    Einzelhost-URL kein Prozentzeichen enthält.

    Geprüft wird die echte Eigenschaft, nicht das Idiom: Wer `alembic_url`
    wieder auf `async_url` zurückdreht, bekommt hier einen roten Test statt
    eines roten Deploys.
    """
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_HOSTS", "pg-a,pg-b:5433,pg-c")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_DATABASE", "edutap")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_USER", "ddl")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_PASSWORD", "s3cret")
    settings = DatabaseSettings()
    assert "%3A" in settings.async_url, "sonst prüfte dieser Test nichts"

    config = Config()
    config.set_main_option("sqlalchemy.url", settings.alembic_url)

    assert config.get_main_option("sqlalchemy.url") == settings.async_url


def test_the_unescaped_url_would_have_failed(monkeypatch):
    """Die Gegenprobe -- ein Wächter, der immer hält, fällt nicht auf."""
    import pytest

    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_HOSTS", "pg-a,pg-b:5433")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_DATABASE", "edutap")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_USER", "ddl")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DB_PASSWORD", "s3cret")

    with pytest.raises(ValueError, match="interpolation"):
        Config().set_main_option("sqlalchemy.url", DatabaseSettings().async_url)
