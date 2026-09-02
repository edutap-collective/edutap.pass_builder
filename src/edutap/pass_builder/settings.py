"""Application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from edutap.db_definitions.settings import ASYNC_DRIVER, ClusterSettings
from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRETS_DIR = "/run/secrets"
"""Where an orchestrator mounts secrets.

pydantic-settings reads from here, so the master key, the database password
and the object-store key arrive as files instead of environment values -- and
are then never in the process environment at all, which means never in
`docker inspect` and never in a frame local an error tracker collects.

Two things worth knowing before wiring this up:

* **The file name carries the `env_prefix`**, so the master key is read from
  `/run/secrets/EDUTAP_PASS_BUILDER_secret_master_key`, not `.../secret_master_key`.
  A secret mounted under the bare field name is silently ignored -- there is no
  `_FILE` convention in pydantic-settings.
* **A missing directory is harmless.** pydantic-settings emits a `UserWarning`
  and falls back to the environment, so a developer without `/run/secrets` is
  not blocked. That is why this can be the default rather than a switch.
"""


class DatabaseSettings(ClusterSettings):
    """How this service reaches its Postgres cluster, prefix `EDUTAP_PASS_BUILDER_DB_`.

    Everything about *reaching* a cluster -- naming every node, asking for the
    one that accepts writes, spelling TLS the way each driver wants it -- comes
    from :class:`edutap.db_definitions.settings.ClusterSettings`.

    Until 2026-09-01 this service read a single `database_url`. That form names
    one node, which is what breaks at the next failover, and it carries the
    password inside the string, which is what keeps it in the environment.

    **The four fields are re-declared without defaults on purpose.** The base
    gives them ones that suit a development machine, and a default is exactly
    wrong here: a deployment that misspells the prefix would then start cleanly
    and write into *some* database rather than abort.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_PASS_BUILDER_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=SECRETS_DIR,
        extra="ignore",
    )

    #: Every node of the cluster, comma separated -- see :class:`ClusterSettings`.
    hosts: str
    database: str
    user: str
    password: SecretStr

    @property
    def async_url(self) -> str:
        """Return the DSN for the async driver this service uses."""
        return self.url(ASYNC_DRIVER)

    @property
    def alembic_url(self) -> str:
        """The same DSN, escaped for Alembic's `Config.set_main_option`.

        THE DOUBLED PERCENT SIGNS ARE LOAD-BEARING. `set_main_option` writes
        into a `ConfigParser`, and that reads `%` as interpolation syntax. A
        multi-host URL carries the node list as repeated
        `host=<name>%3A<port>` query parameters -- the colons are
        percent-encoded -- and every one of them looks to `ConfigParser` like a
        broken `%(name)s`. The run then aborts before touching the database at
        all:

            ValueError: invalid interpolation syntax in
            'postgresql+asyncpg://...?host=pg18-...%3A5432&...' at position 119

        It never came up while this service read a single `database_url`: that
        form has no percent sign. The multi-host URL is the first that does, and
        this package is the first to combine `ClusterSettings` with an Alembic
        of its own -- `edutap.image_service` uses the class too, but its tables
        live in `public` and are migrated by the shared `edutap-dbdef`
        container.

        A named property rather than a `.replace()` in `env.py`, so the next
        package copies a thing with a reason attached instead of an idiom, and
        so a test can hold it. The proper home is `ClusterSettings` itself; that
        is a change to `edutap.db_definitions` and does not belong in the fix
        that unblocks a deploy.
        """
        return self.async_url.replace("%", "%%")


class Settings(BaseSettings):
    """Configuration for the pass builder service."""

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_PASS_BUILDER_",
        env_file=".env",
        secrets_dir=SECRETS_DIR,
        extra="ignore",
    )

    secret_master_key: SecretStr
    """Base64 encoded 32 byte AES key wrapping the per-secret data keys."""

    data_provider_base_url: str
    data_provider_token: SecretStr = SecretStr("")
    data_provider_timeout: float = 10.0

    image_service_base_url: str = "http://image_service:8000"
    """Where an `IMAGE` mapping rule's reference is fetched from.

    A rule binds a URL and this service fetches the bytes. It used to bind
    `bytes` directly, which could never work: the data provider answers JSON,
    and JSON has no bytes -- so the value arrived as a string, the asset was
    never written, and the pass published green without its picture.
    """

    image_service_token: SecretStr = SecretStr("")
    image_service_timeout: float = 10.0

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

    # ---- The management UI ----
    #
    # A second ASGI application out of the same image, at a different mount
    # point and with a different notion of a caller: `app.py` authenticates an
    # `api_client` by bearer token, `ui.py` authenticates a person.
    #
    # Two applications rather than two routers on one, because `api_class`
    # above is a single setting for a whole application and it is what keeps
    # `POST /passes` off a publicly reachable entry point. Splitting by router
    # would make that boundary a label, and a label is the kind of boundary
    # that falls silently during the next rework.

    ui_root_path: str = "/portale/edutap-pass-builder"
    """Where the management UI is mounted, in full.

    A PORTAL, NOT AN API. `/api/<domain>/<service>/v<n>` is the namespace for
    REST backends that another program calls; this is an interface a person
    opens, and those live under `/portale/<name>` beside the pass designer,
    the Kafka UI and CloudBeaver.

    The distinction is not cosmetic. A single-page application owns a whole
    subtree -- its assets are fetched from `<root>/assets/...` -- and under
    `/api/wallet` it would squat on the prefix `image-tools` and `admin`
    share, needing a web-frontend rule of its own just for `assets`. Under a
    portal path one rule covers the page, its assets and its API.

    Not composed from `api_class`/`api_domain` like `base_path`: those spell
    the REST convention, and this is not one.
    """

    ui_remote_user_header: str = "REMOTE_USER"
    """Header carrying the authenticated principal, set by the web frontend.

    A header and not an environment variable: the service sits behind the
    frontend rather than inside it. Configurable because the name is a
    deployment's choice, and no deployment should have to patch code for it.
    """

    ui_groups_header: str = "isMemberOf"
    """Header carrying the principal's group memberships, semicolon separated.

    The eduPerson attribute name Shibboleth uses by default. Absent or empty
    means no groups, which is not an error -- it is simply someone who is not
    a member of anything.
    """

    ui_authorised_users: str = ""
    """Principals allowed into the UI, comma separated."""

    ui_authorised_groups: str = ""
    """Groups whose members are allowed into the UI, comma separated."""

    @property
    def base_path(self) -> str:
        """Mount point of this service, fed into FastAPI's root_path."""
        return f"/{self.api_class}{self.api_suffix}/{self.api_domain}"

    @property
    def ui_authorised_user_set(self) -> frozenset[str]:
        """The allow-listed principals, empty entries dropped."""
        return _comma_set(self.ui_authorised_users)

    @property
    def ui_authorised_group_set(self) -> frozenset[str]:
        """The allow-listed groups, empty entries dropped."""
        return _comma_set(self.ui_authorised_groups)


def _comma_set(raw: str) -> frozenset[str]:
    """Split a comma separated setting into a set, dropping blanks.

    A plain `str` field rather than a `list[str]`: pydantic-settings parses a
    list-typed field as JSON, so `a,b` would have to be written `["a","b"]` in
    an environment variable and in a `.env` -- a shape nobody types correctly
    twice.
    """
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return the process-wide database settings.

    Separate from :func:`get_settings` because it is a separate class with a
    separate prefix, and because the two are read at different moments: the
    engine needs this one, every request needs the other.
    """
    return DatabaseSettings()
