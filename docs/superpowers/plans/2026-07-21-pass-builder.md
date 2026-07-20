# edutap.pass_builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateless FastAPI service that renders Apple `.pkpass` files and Google wallet objects from versioned, database-stored templates plus person data fetched from `edutap.data_provider`.

**Architecture:** Four layers — routers (FastAPI), services (lifecycle and orchestration), a pure engine (substitution and building, no I/O), adapters (data_provider, object store, secret backend). The engine is testable with literals only. Templates, variants, versions, mapping rules and credentials are persisted; issued passes are not.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, pydantic-settings, SQLModel on async SQLAlchemy, PostgreSQL 18, Alembic, httpx, `edutap.wallet_apple`, `edutap.wallet_google`, `cryptography`, `aioboto3` against RustFS.

**Spec:** `docs/superpowers/specs/2026-07-21-pass-builder-design.md`

## Global Constraints

- Python `>=3.12`; runtime image `python:3.14-slim`; PostgreSQL 18.
- Async throughout. No blocking calls in async paths.
- Documentation, code, comments, identifiers and commit messages in English. Conventional Commits.
- `tenant_id` is never read from a request path or body — only from the authenticated API client.
- No endpoint ever returns secret key material, not even masked.
- No person data and no secret in any log line or error message.
- Errors are `application/problem+json` (RFC 9457) with a stable `type` slug.
- Published `template_version` rows and everything attached to them are immutable.
- The engine never formats dates. Dates are emitted as ISO 8601 with time zone.
- Every mapping rule's `source_field` is validated against the cached `data_provider` catalogue on save.
- Ruff rule groups `E,F,W,B,I,UP,D,S`; line length 88. `ty` for type checking.
- Every task ends with `make lint` and `make test-local` green before the commit.

## Prerequisites

| Prerequisite | Status | Handling in this plan |
|---|---|---|
| `edutap.wallet_apple.api.from_template(file)` | PR #39 open | Task 15 depends on it. Until merged, install `wallet_apple` from the `feature/from-template` branch. |
| `edutap.wallet_google` `${…}` resolution | not started | **Deviation from the spec, flagged for confirmation:** implemented as a pure module in this repo (Task 16), with Task 22 upstreaming it to `wallet_google`. |

---

### Task 1: Project scaffolding, settings and application skeleton

**Files:**
- Create: `pyproject.toml`, `Makefile`, `tox.ini`, `.pre-commit-config.yaml`, `.gitignore`, `README.md`, `LICENSE`
- Create: `src/edutap/pass_builder/__init__.py`, `settings.py`, `errors.py`, `app.py`, `py.typed`
- Create: `src/edutap/pass_builder/routers/__init__.py`, `routers/health.py`
- Test: `tests/test_settings.py`, `tests/test_errors.py`, `tests/test_health.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, prefix `EDUTAP_PASS_BUILDER_`), `get_settings() -> Settings`, `ProblemError(status, slug, title, detail=None, **extra)`, `install_error_handlers(app)`, `create_app() -> FastAPI`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "edutap.pass_builder"
version = "0.1.0"
description = "Stateless service building Apple and Google wallet passes from versioned templates"
readme = "README.md"
requires-python = ">=3.12"
license = "EUPL-1.2"
authors = [{ name = "eduTAP" }]
dependencies = [
    "aioboto3>=13",
    "alembic>=1.13",
    "asyncpg>=0.29",
    "cryptography>=43",
    "edutap.wallet-apple>=1.0.0a2",
    "edutap.wallet-google>=3.0.0b1",
    "fastapi>=0.115",
    "httpx>=0.27",
    "pydantic>=2.8",
    "pydantic-settings>=2.4",
    "sqlmodel>=0.0.22",
    "uvicorn[standard]>=0.30",
]

[project.optional-dependencies]
dev = [
    "pdbp",
    "pytest>=8.2",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "ruff>=0.6",
    "testcontainers[postgres]>=4.8",
    "ty",
]
docs = ["myst-parser", "sphinx>=8"]

[tool.hatch.build.targets.wheel]
packages = ["src/edutap"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
asyncio_mode = "auto"
markers = [
    "integration: needs docker compose services and a test certificate",
]

[tool.ruff]
line-length = 88
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "B", "I", "UP", "D", "S"]
ignore = ["D203", "D213"]

[tool.ruff.lint.pydocstyle]
convention = "pep257"

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["D", "S101"]

[tool.ty.environment]
root = ["./src"]
```

- [ ] **Step 2: Create `Makefile`**

```makefile
.PHONY: install lint reformat test-local test-integration

install:
	uv venv
	uv pip install -U -e ".[dev,docs]"

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check

reformat:
	uv run ruff format src tests
	uv run ruff check --fix src tests

test-local:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration
```

- [ ] **Step 3: Write the failing settings test**

```python
# tests/test_settings.py
from edutap.pass_builder.settings import Settings


def test_settings_read_prefixed_environment(monkeypatch):
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY", "a" * 44)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL", "http://dp")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://x/y"
    assert settings.audit_retention_months == 24
    assert settings.data_provider_timeout == 10.0
```

- [ ] **Step 4: Run it and confirm it fails**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.pass_builder.settings'`

- [ ] **Step 5: Implement `settings.py`**

```python
"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


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
    return Settings()  # ty: ignore[missing-argument]
```

- [ ] **Step 6: Run the settings test**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 7: Write the failing error-model test**

```python
# tests/test_errors.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.errors import install_error_handlers


def build_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise ProblemError(422, "missing_field", "Missing fields", fields=["person.name"])

    return TestClient(app)


def test_problem_error_is_rendered_as_problem_json():
    response = build_client().get("/boom")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "urn:edutap:pass-builder:missing_field"
    assert body["title"] == "Missing fields"
    assert body["status"] == 422
    assert body["fields"] == ["person.name"]
```

- [ ] **Step 8: Run it and confirm it fails**

Run: `uv run pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.pass_builder.errors'`

- [ ] **Step 9: Implement `errors.py`**

```python
"""RFC 9457 problem responses with stable machine readable slugs."""

from typing import Any

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

PROBLEM_TYPE_PREFIX = "urn:edutap:pass-builder:"


class ProblemError(Exception):
    """An error that is rendered as an application/problem+json response."""

    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        """Return the problem document body."""
        body: dict[str, Any] = {
            "type": f"{PROBLEM_TYPE_PREFIX}{self.slug}",
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handler on the application."""

    @app.exception_handler(ProblemError)
    async def _handle(_: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content=error.to_dict(),
            media_type="application/problem+json",
        )
```

- [ ] **Step 10: Run the error test**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS

- [ ] **Step 11: Write the failing health test**

```python
# tests/test_health.py
from fastapi.testclient import TestClient

from edutap.pass_builder.app import create_app


def test_healthz_reports_alive():
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 12: Run it and confirm it fails**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.pass_builder.app'`

- [ ] **Step 13: Implement `routers/health.py` and `app.py`**

```python
# src/edutap/pass_builder/routers/health.py
"""Liveness and readiness endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report that the process is alive."""
    return {"status": "ok"}
```

```python
# src/edutap/pass_builder/app.py
"""FastAPI application factory."""

from fastapi import FastAPI

from .errors import install_error_handlers
from .routers import health


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="eduTAP pass builder",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    install_error_handlers(app)
    app.include_router(health.router)
    return app
```

- [ ] **Step 14: Run the full local suite and the linter**

Run: `make lint && make test-local`
Expected: ruff clean, `ty` clean, 3 tests passed

- [ ] **Step 15: Commit**

```bash
git add pyproject.toml Makefile tox.ini .pre-commit-config.yaml .gitignore README.md LICENSE src tests
git commit -m "feat: scaffold service with settings, problem+json errors and health endpoint"
```

---

### Task 2: Test infrastructure — compose services and database fixtures

**Files:**
- Create: `compose.yml`, `Dockerfile`
- Create: `tests/conftest.py`
- Test: `tests/test_database_fixture.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: pytest fixtures `postgres_url` (session scoped, testcontainers), `engine`, `session` (function scoped, rolled back), and `anyio_backend`.

- [ ] **Step 1: Create `compose.yml`**

```yaml
services:
  db:
    image: postgres:18-alpine
    environment:
      POSTGRES_USER: pass_builder
      POSTGRES_PASSWORD: pass_builder
      POSTGRES_DB: pass_builder
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pass_builder"]
      interval: 5s
      retries: 10

  objectstore:
    image: rustfs/rustfs:latest
    environment:
      RUSTFS_ACCESS_KEY: pass_builder
      RUSTFS_SECRET_KEY: pass_builder
    ports: ["9000:9000"]

  app:
    build: .
    depends_on:
      db: { condition: service_healthy }
    environment:
      EDUTAP_PASS_BUILDER_DATABASE_URL: postgresql+asyncpg://pass_builder:pass_builder@db/pass_builder
      EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY: ${EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY}
      EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL: http://data-provider
      EDUTAP_PASS_BUILDER_OBJECTSTORE_ENDPOINT_URL: http://objectstore:9000
      EDUTAP_PASS_BUILDER_OBJECTSTORE_ACCESS_KEY: pass_builder
      EDUTAP_PASS_BUILDER_OBJECTSTORE_SECRET_KEY: pass_builder
    ports: ["8000:8000"]
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.14-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

FROM python:3.14-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY assets /app/assets
WORKDIR /app
USER app
EXPOSE 8000
CMD ["uvicorn", "edutap.pass_builder.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Write the failing fixture test**

```python
# tests/test_database_fixture.py
from sqlalchemy import text


async def test_session_talks_to_postgres_18(session):
    result = await session.execute(text("SHOW server_version_num"))
    assert int(result.scalar_one()) >= 180000
```

- [ ] **Step 4: Run it and confirm it fails**

Run: `uv run pytest tests/test_database_fixture.py -v`
Expected: FAIL — `fixture 'session' not found`

- [ ] **Step 5: Implement `tests/conftest.py`**

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
async def engine(postgres_url):
    engine = create_async_engine(postgres_url, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with maker() as session:
        yield session
    await transaction.rollback()
    await connection.close()
```

- [ ] **Step 6: Run the fixture test**

Run: `uv run pytest tests/test_database_fixture.py -v`
Expected: PASS (first run pulls the `postgres:18-alpine` image)

- [ ] **Step 7: Commit**

```bash
git add compose.yml Dockerfile tests/conftest.py tests/test_database_fixture.py
git commit -m "test: add compose services and PostgreSQL 18 test fixtures"
```

---

### Task 3: Database models and the initial migration

**Files:**
- Create: `src/edutap/pass_builder/models/__init__.py`, `models/enums.py`, `models/db.py`
- Create: `src/edutap/pass_builder/database.py`
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `session` fixture from Task 2.
- Produces: SQLModel tables `Tenant`, `ApiClient`, `CredentialSet`, `SecretBlob`, `Template`, `TemplateVariant`, `TemplateVersion`, `TemplateAsset`, `MappingRule`, `DataField`, `AuditLog`; enums `WalletType`, `Provider`, `CredentialStatus`, `VersionStatus`, `RuleOrigin`, `TargetKind`, `ValueType`, `SecretKind`; `get_session()` dependency.

- [ ] **Step 1: Write the failing constraint tests**

```python
# tests/test_models.py
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from edutap.pass_builder.models.db import Template
from edutap.pass_builder.models.db import TemplateVariant
from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.models.enums import WalletType


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


async def make_template(session) -> Template:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    template = Template(tenant_id=tenant.id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()
    return template


async def test_only_one_default_variant_per_wallet_type(session):
    template = await make_template(session)
    session.add(
        TemplateVariant(
            template_id=template.id,
            wallet_type=WalletType.APPLE,
            key="student",
            name="Student",
            is_default=True,
        )
    )
    await session.flush()
    session.add(
        TemplateVariant(
            template_id=template.id,
            wallet_type=WalletType.APPLE,
            key="staff",
            name="Staff",
            is_default=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_template_key_is_unique_per_tenant(session):
    template = await make_template(session)
    session.add(Template(tenant_id=template.tenant_id, key="student-id", name="Copy"))
    with pytest.raises(IntegrityError):
        await session.flush()
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.pass_builder.models'`

- [ ] **Step 3: Implement `models/enums.py`**

```python
"""Enumerations shared by the database models and the API schemas."""

from enum import StrEnum


class WalletType(StrEnum):
    """Wallet platform a variant targets."""

    APPLE = "apple"
    GOOGLE = "google"
    SAMSUNG = "samsung"


class Provider(StrEnum):
    """Credential provider."""

    APPLE = "apple"
    GOOGLE = "google"


class CredentialStatus(StrEnum):
    """Lifecycle state of a credential set."""

    KEY_PENDING = "key_pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class VersionStatus(StrEnum):
    """Lifecycle state of a template version."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RuleOrigin(StrEnum):
    """Whether a mapping rule was authored or derived on publish."""

    AUTHORED = "authored"
    DERIVED = "derived"


class TargetKind(StrEnum):
    """What a mapping rule writes into the pass."""

    FIELD_VALUE = "field_value"
    FIELD_LABEL = "field_label"
    BARCODE_MESSAGE = "barcode_message"
    BARCODE_ALT_TEXT = "barcode_alt_text"
    IMAGE = "image"
    NFC_PAYLOAD = "nfc_payload"
    JSON_POINTER = "json_pointer"


class ValueType(StrEnum):
    """Type of the value a mapping rule binds."""

    TEXT = "text"
    DATE = "date"
    NUMBER = "number"
    BOOLEAN = "boolean"
    IMAGE = "image"
    URI = "uri"


class SecretKind(StrEnum):
    """Kind of secret material stored for a credential set."""

    PRIVATE_KEY = "private_key"
    SERVICE_ACCOUNT_JSON = "service_account_json"


class Scope(StrEnum):
    """API client scopes."""

    RENDER = "render"
    MANAGE = "manage"
    CREDENTIALS = "credentials"
```

- [ ] **Step 4: Implement `models/db.py`**

Write one SQLModel class per table from spec section 3, in this order: `Tenant`, `ApiClient`, `CredentialSet`, `SecretBlob`, `Template`, `TemplateVariant`, `TemplateVersion`, `TemplateAsset`, `MappingRule`, `DataField`, `AuditLog`. Every class carries `id: UUID = Field(default_factory=uuid4, primary_key=True)` and `created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))`. Example for the two classes the tests touch, and the shape every other class follows:

```python
"""SQLModel table definitions."""

from datetime import UTC
from datetime import datetime
from uuid import UUID
from uuid import uuid4

from sqlalchemy import Index
from sqlalchemy import UniqueConstraint
from sqlmodel import Field
from sqlmodel import SQLModel

from .enums import WalletType


class Tenant(SQLModel, table=True):
    """An organisational unit owning templates and credentials."""

    __tablename__ = "tenant"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(unique=True, index=True)
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Template(SQLModel, table=True):
    """A logical credential, for example a student identity card."""

    __tablename__ = "template"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenant.id", index=True)
    key: str
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None


class TemplateVariant(SQLModel, table=True):
    """One design of a template for one wallet platform."""

    __tablename__ = "template_variant"
    __table_args__ = (
        UniqueConstraint("template_id", "wallet_type", "key"),
        Index(
            "uq_variant_default",
            "template_id",
            "wallet_type",
            unique=True,
            postgresql_where="is_default",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    template_id: UUID = Field(foreign_key="template.id", index=True)
    wallet_type: WalletType
    key: str
    name: str
    is_default: bool = False
    credential_set_id: UUID | None = Field(default=None, foreign_key="credential_set.id")
    google_class_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None
```

`TemplateVersion` uses `sa_column=Column(JSONB)` for `pass_json`, `class_json` and `object_json`, a partial unique index `uq_version_published` on `variant_id` where `status = 'published'`, and a `UniqueConstraint("variant_id", "number")`. `SecretBlob` uses `bytes` columns for `ciphertext`, `nonce` and `wrapped_dek`. `AuditLog` stores `requested_fields: list[str]` with `sa_column=Column(ARRAY(String))` and `details` as `JSONB`.

- [ ] **Step 5: Run the model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS — both constraint violations raise `IntegrityError`

- [ ] **Step 6: Implement `database.py`**

```python
"""Database engine and session dependency."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine."""
    return create_async_engine(get_settings().database_url, future=True, pool_pre_ping=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for one request."""
    maker = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    async with maker() as session:
        yield session
```

- [ ] **Step 7: Generate and review the initial migration**

Run: `uv run alembic revision --autogenerate -m "initial schema"`
Then rename the generated file to `migrations/versions/0001_initial.py` and verify by inspection that both partial unique indexes (`uq_variant_default`, `uq_version_published`) and all `CHECK` constraints from the spec are present. Autogenerate does not emit partial indexes reliably — add them by hand as `op.create_index(..., postgresql_where=sa.text("is_default"))` if missing.

- [ ] **Step 8: Verify the migration applies cleanly**

Run: `docker compose up -d db && uv run alembic upgrade head && uv run alembic downgrade base`
Expected: both directions succeed without error

- [ ] **Step 9: Commit**

```bash
git add src/edutap/pass_builder/models src/edutap/pass_builder/database.py alembic.ini migrations tests/test_models.py
git commit -m "feat: add database models and initial migration"
```

---

### Task 4: Engine value binding and type conversion

**Files:**
- Create: `src/edutap/pass_builder/engine/__init__.py`, `engine/spec.py`, `engine/binding.py`
- Test: `tests/engine/test_binding.py`

**Interfaces:**
- Consumes: `ValueType`, `TargetKind`, `RuleOrigin` enums from Task 3.
- Produces:
  - `RuleSpec` (Pydantic: `target_kind`, `target`, `source_field`, `value_type`, `required`, `default_value`, `position`)
  - `RenderSpec` (Pydantic: `wallet_type`, `pass_json`, `class_json`, `object_json`, `assets: dict[str, bytes]`, `rules: list[RuleSpec]`, `nfc_enabled`, `nfc_encryption_public_key`, `nfc_requires_authentication`, `issuer_id`)
  - `BoundValue` (Pydantic: `rule: RuleSpec`, `value: str | bytes`)
  - `MissingFieldsError(fields: list[str])`
  - `bind(rules: list[RuleSpec], data: Mapping[str, Any]) -> list[BoundValue]`
  - `required_fields(rules: list[RuleSpec]) -> list[str]`

- [ ] **Step 1: Write the failing binding tests**

```python
# tests/engine/test_binding.py
from datetime import date

import pytest

from edutap.pass_builder.engine.binding import MissingFieldsError
from edutap.pass_builder.engine.binding import bind
from edutap.pass_builder.engine.binding import required_fields
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType


def rule(source_field, value_type=ValueType.TEXT, required=True, default_value=None):
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE,
        target="name",
        source_field=source_field,
        value_type=value_type,
        required=required,
        default_value=default_value,
        position=0,
    )


def test_date_is_converted_to_iso_8601():
    bound = bind([rule("person.valid_until", ValueType.DATE)],
                 {"person.valid_until": date(2027, 3, 31)})
    assert bound[0].value == "2027-03-31"


def test_number_uses_canonical_decimal():
    bound = bind([rule("person.credits", ValueType.NUMBER)], {"person.credits": 42})
    assert bound[0].value == "42"


def test_missing_required_field_lists_all_missing_at_once():
    rules = [rule("person.name"), rule("person.email")]
    with pytest.raises(MissingFieldsError) as excinfo:
        bind(rules, {})
    assert excinfo.value.fields == ["person.name", "person.email"]


def test_default_value_is_used_when_field_absent():
    bound = bind([rule("person.title", required=False, default_value="—")], {})
    assert bound[0].value == "—"


def test_required_fields_are_deduplicated_and_ordered():
    rules = [rule("person.name"), rule("person.name"), rule("person.email")]
    assert required_fields(rules) == ["person.name", "person.email"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/engine/test_binding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.pass_builder.engine.binding'`

- [ ] **Step 3: Implement `engine/spec.py`**

```python
"""Pure input models for the rendering engine."""

from pydantic import BaseModel

from ..models.enums import TargetKind
from ..models.enums import ValueType
from ..models.enums import WalletType


class RuleSpec(BaseModel):
    """A single substitution rule, decoupled from the database row."""

    target_kind: TargetKind
    target: str
    source_field: str
    value_type: ValueType
    required: bool = True
    default_value: str | None = None
    position: int = 0


class RenderSpec(BaseModel):
    """Everything the engine needs to render one pass, free of I/O."""

    wallet_type: WalletType
    pass_json: dict | None = None
    class_json: dict | None = None
    object_json: dict | None = None
    assets: dict[str, bytes] = {}
    rules: list[RuleSpec] = []
    nfc_enabled: bool = False
    nfc_encryption_public_key: str | None = None
    nfc_requires_authentication: bool = False
    issuer_id: str | None = None


class BoundValue(BaseModel):
    """A rule paired with its resolved, converted value."""

    model_config = {"arbitrary_types_allowed": True}

    rule: RuleSpec
    value: str | bytes
```

- [ ] **Step 4: Implement `engine/binding.py`**

```python
"""Bind data-provider values to mapping rules and convert their types."""

from collections.abc import Mapping
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..models.enums import ValueType
from .spec import BoundValue
from .spec import RuleSpec


class MissingFieldsError(Exception):
    """Raised when required source fields are absent from the data."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(f"missing required fields: {', '.join(fields)}")
        self.fields = fields


def required_fields(rules: list[RuleSpec]) -> list[str]:
    """Return the deduplicated, order-preserving list of required source fields."""
    seen: dict[str, None] = {}
    for rule in rules:
        if rule.required:
            seen.setdefault(rule.source_field, None)
    return list(seen)


def _convert(value: Any, value_type: ValueType) -> str | bytes:
    if value_type == ValueType.DATE:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
    if value_type == ValueType.NUMBER:
        return str(Decimal(str(value)).normalize())
    if value_type == ValueType.BOOLEAN:
        return "true" if value else "false"
    if value_type == ValueType.IMAGE and isinstance(value, bytes):
        return value
    return str(value)


def bind(rules: list[RuleSpec], data: Mapping[str, Any]) -> list[BoundValue]:
    """Resolve every rule against the data, collecting all missing fields."""
    bound: list[BoundValue] = []
    missing: list[str] = []
    for rule in rules:
        if rule.source_field in data:
            raw = data[rule.source_field]
        elif rule.default_value is not None:
            raw = rule.default_value
        elif rule.required:
            if rule.source_field not in missing:
                missing.append(rule.source_field)
            continue
        else:
            continue
        bound.append(BoundValue(rule=rule, value=_convert(raw, rule.value_type)))
    if missing:
        raise MissingFieldsError(missing)
    return bound
```

- [ ] **Step 5: Run the binding tests**

Run: `uv run pytest tests/engine/test_binding.py -v`
Expected: PASS — 5 tests

- [ ] **Step 6: Run linter and full local suite**

Run: `make lint && make test-local`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add src/edutap/pass_builder/engine tests/engine
git commit -m "feat: add engine value binding and type conversion"
```

---

### Task 5: Google placeholder resolution

**Files:**
- Create: `src/edutap/pass_builder/engine/placeholders.py`
- Test: `tests/engine/test_placeholders.py`

**Interfaces:**
- Produces:
  - `scan_placeholders(obj: Any) -> list[tuple[str, str]]` returning `(json_pointer, source_field)` pairs for every `${…}` occurrence in string values
  - `resolve_placeholders(obj: Any, values: Mapping[str, str]) -> Any` returning a deep copy with `${…}` replaced in string values only

**Note:** This is the deviation flagged in Prerequisites. This module is written pure and self-contained so Task 22 can move it into `edutap.wallet_google` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_placeholders.py
from edutap.pass_builder.engine.placeholders import resolve_placeholders
from edutap.pass_builder.engine.placeholders import scan_placeholders


def test_scan_returns_pointer_and_field_for_each_placeholder():
    obj = {"header": {"value": "${person.name}"}, "sub": [{"value": "${person.id}"}]}
    assert scan_placeholders(obj) == [
        ("/header/value", "person.name"),
        ("/sub/0/value", "person.id"),
    ]


def test_resolve_replaces_string_values_only():
    obj = {"value": "${person.name}", "person.name": "not-a-placeholder"}
    result = resolve_placeholders(obj, {"person.name": "Ada"})
    assert result["value"] == "Ada"
    assert result["person.name"] == "not-a-placeholder"


def test_dollar_dollar_is_an_escape_for_a_literal_dollar():
    obj = {"value": "price $$5"}
    assert resolve_placeholders(obj, {})["value"] == "price $5"


def test_placeholder_inside_surrounding_text_is_substituted():
    obj = {"value": "Hello ${person.name}!"}
    assert resolve_placeholders(obj, {"person.name": "Ada"})["value"] == "Hello Ada!"


def test_keys_are_never_touched():
    obj = {"${person.name}": "value"}
    assert list(resolve_placeholders(obj, {"person.name": "x"})) == ["${person.name}"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/engine/test_placeholders.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `engine/placeholders.py`**

```python
"""Resolve ${field} placeholders inside a JSON-like structure.

The syntax is deliberately minimal: ${dotted.field} is replaced by a looked-up
value, $$ is a literal dollar sign, and replacement happens in string values
only — never in dictionary keys. No filters, no expressions, no code.
"""

import re
from collections.abc import Mapping
from typing import Any

_TOKEN = re.compile(r"\$\$|\$\{([^}]+)\}")


def _walk(obj: Any, pointer: str, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, str):
        for match in _TOKEN.finditer(obj):
            if match.group(1) is not None:
                out.append((pointer, match.group(1)))
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            _walk(value, f"{pointer}/{_escape(str(key))}", out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk(value, f"{pointer}/{index}", out)


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def scan_placeholders(obj: Any) -> list[tuple[str, str]]:
    """Return (json_pointer, source_field) for every ${…} in a string value."""
    out: list[tuple[str, str]] = []
    _walk(obj, "", out)
    return out


def _substitute(text: str, values: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        return values.get(match.group(1), match.group(0))

    return _TOKEN.sub(replace, text)


def resolve_placeholders(obj: Any, values: Mapping[str, str]) -> Any:
    """Return a deep copy with ${…} resolved in string values only."""
    if isinstance(obj, str):
        return _substitute(obj, values)
    if isinstance(obj, Mapping):
        return {key: resolve_placeholders(value, values) for key, value in obj.items()}
    if isinstance(obj, list):
        return [resolve_placeholders(value, values) for value in obj]
    return obj
```

- [ ] **Step 4: Run the placeholder tests**

Run: `uv run pytest tests/engine/test_placeholders.py -v`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/engine/placeholders.py tests/engine/test_placeholders.py
git commit -m "feat: add Google placeholder scanning and resolution"
```

---

### Task 6: Apple field and asset application

**Files:**
- Create: `src/edutap/pass_builder/engine/apple_apply.py`
- Test: `tests/engine/test_apple_apply.py`

**Interfaces:**
- Consumes: `BoundValue`, `RuleSpec` from Task 4; `TargetKind` from Task 3.
- Produces:
  - `NfcPayloadTooLongError(length: int)`
  - `apply_apple(pass_json: dict, assets: dict[str, bytes], bound: list[BoundValue]) -> tuple[dict, dict[str, bytes]]` returning the mutated pass dict and asset map. Locates a `field_value`/`field_label` by its `key` across `headerFields`, `primaryFields`, `secondaryFields`, `auxiliaryFields`, `backFields` of every pass style. `image` replaces `assets[target]`. `nfc_payload` sets `pass_json["nfc"]["message"]` and rejects values longer than 64 characters. `barcode_message` sets `barcodes[0].message`. `json_pointer` writes via RFC 6901 without creating intermediate nodes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_apple_apply.py
import pytest

from edutap.pass_builder.engine.apple_apply import NfcPayloadTooLongError
from edutap.pass_builder.engine.apple_apply import apply_apple
from edutap.pass_builder.engine.spec import BoundValue
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType


def bound(target_kind, target, value):
    return BoundValue(
        rule=RuleSpec(
            target_kind=target_kind,
            target=target,
            source_field="x",
            value_type=ValueType.TEXT,
        ),
        value=value,
    )


def base_pass():
    return {
        "generic": {"primaryFields": [{"key": "name", "label": "Name", "value": ""}]},
        "barcodes": [{"message": "", "format": "PKBarcodeFormatQR"}],
    }


def test_field_value_is_written_by_key():
    result, _ = apply_apple(base_pass(), {}, [bound(TargetKind.FIELD_VALUE, "name", "Ada")])
    assert result["generic"]["primaryFields"][0]["value"] == "Ada"


def test_image_replaces_asset():
    _, assets = apply_apple(base_pass(), {"icon.png": b"old"},
                            [bound(TargetKind.IMAGE, "icon.png", b"new")])
    assert assets["icon.png"] == b"new"


def test_barcode_message_is_written():
    result, _ = apply_apple(base_pass(), {}, [bound(TargetKind.BARCODE_MESSAGE, "", "PAYLOAD")])
    assert result["barcodes"][0]["message"] == "PAYLOAD"


def test_nfc_payload_over_64_chars_is_rejected():
    with pytest.raises(NfcPayloadTooLongError):
        apply_apple(base_pass(), {}, [bound(TargetKind.NFC_PAYLOAD, "", "x" * 65)])


def test_nfc_payload_within_limit_is_written():
    result, _ = apply_apple(base_pass(), {}, [bound(TargetKind.NFC_PAYLOAD, "", "token")])
    assert result["nfc"]["message"] == "token"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/engine/test_apple_apply.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `engine/apple_apply.py`**

```python
"""Apply bound values to an Apple pass.json structure and its assets."""

from typing import Any

from ..models.enums import TargetKind
from .spec import BoundValue

_FIELD_GROUPS = (
    "headerFields",
    "primaryFields",
    "secondaryFields",
    "auxiliaryFields",
    "backFields",
)
_STYLES = ("boardingPass", "coupon", "eventTicket", "generic", "storeCard")
_NFC_MAX = 64


class NfcPayloadTooLongError(Exception):
    """Raised when an Apple NFC message exceeds 64 characters."""

    def __init__(self, length: int) -> None:
        super().__init__(f"nfc payload has {length} characters, limit is {_NFC_MAX}")
        self.length = length


def _set_field(pass_json: dict, key: str, attribute: str, value: str) -> None:
    for style in _STYLES:
        style_block = pass_json.get(style)
        if not isinstance(style_block, dict):
            continue
        for group in _FIELD_GROUPS:
            for field in style_block.get(group, []):
                if field.get("key") == key:
                    field[attribute] = value


def _set_pointer(obj: Any, pointer: str, value: str) -> None:
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    cursor = obj
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    last = parts[-1]
    if isinstance(cursor, list):
        cursor[int(last)] = value
    else:
        cursor[last] = value


def apply_apple(
    pass_json: dict,
    assets: dict[str, bytes],
    bound: list[BoundValue],
) -> tuple[dict, dict[str, bytes]]:
    """Return the pass dict and asset map with all bound values applied."""
    for item in bound:
        kind = item.rule.target_kind
        if kind == TargetKind.FIELD_VALUE:
            _set_field(pass_json, item.rule.target, "value", str(item.value))
        elif kind == TargetKind.FIELD_LABEL:
            _set_field(pass_json, item.rule.target, "label", str(item.value))
        elif kind == TargetKind.IMAGE and isinstance(item.value, bytes):
            assets[item.rule.target] = item.value
        elif kind == TargetKind.BARCODE_MESSAGE:
            pass_json.setdefault("barcodes", [{}])[0]["message"] = str(item.value)
        elif kind == TargetKind.BARCODE_ALT_TEXT:
            pass_json.setdefault("barcodes", [{}])[0]["altText"] = str(item.value)
        elif kind == TargetKind.NFC_PAYLOAD:
            text = str(item.value)
            if len(text) > _NFC_MAX:
                raise NfcPayloadTooLongError(len(text))
            pass_json.setdefault("nfc", {})["message"] = text
        elif kind == TargetKind.JSON_POINTER:
            _set_pointer(pass_json, item.rule.target, str(item.value))
    return pass_json, assets
```

- [ ] **Step 4: Run the apply tests**

Run: `uv run pytest tests/engine/test_apple_apply.py -v`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/engine/apple_apply.py tests/engine/test_apple_apply.py
git commit -m "feat: add Apple field and asset application"
```

---

### Task 7: Google object application

**Files:**
- Create: `src/edutap/pass_builder/engine/google_apply.py`
- Test: `tests/engine/test_google_apply.py`

**Interfaces:**
- Consumes: `BoundValue` from Task 4; `resolve_placeholders` from Task 5.
- Produces: `apply_google(object_json: dict, bound: list[BoundValue]) -> dict` — builds a `{source_field: value}` map from the bound values, then calls `resolve_placeholders(object_json, values)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_google_apply.py
from edutap.pass_builder.engine.google_apply import apply_google
from edutap.pass_builder.engine.spec import BoundValue
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType


def bound(source_field, value):
    return BoundValue(
        rule=RuleSpec(
            target_kind=TargetKind.JSON_POINTER,
            target="/x",
            source_field=source_field,
            value_type=ValueType.TEXT,
        ),
        value=value,
    )


def test_placeholders_are_resolved_from_bound_values():
    object_json = {"cardTitle": {"defaultValue": {"value": "${person.name}"}}}
    result = apply_google(object_json, [bound("person.name", "Ada")])
    assert result["cardTitle"]["defaultValue"]["value"] == "Ada"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/engine/test_google_apply.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `engine/google_apply.py`**

```python
"""Apply bound values to a Google wallet object template."""

from .placeholders import resolve_placeholders
from .spec import BoundValue


def apply_google(object_json: dict, bound: list[BoundValue]) -> dict:
    """Return the object with ${…} placeholders resolved from bound values."""
    values = {item.rule.source_field: str(item.value) for item in bound}
    return resolve_placeholders(object_json, values)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/engine/test_google_apply.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/engine/google_apply.py tests/engine/test_google_apply.py
git commit -m "feat: add Google object application via placeholder resolution"
```

---

### Task 8: Certificate parsing, key generation and CSR

**Files:**
- Create: `src/edutap/pass_builder/crypto/__init__.py`, `crypto/certificates.py`, `crypto/keys.py`
- Create test fixture: `tests/fixtures/apple_cert.pem` (copy `pass.de.lmu.ub` cert from `edutap-settings-certs/apple/certificate-pass.de.lmu.ub.pem`)
- Test: `tests/crypto/test_certificates.py`, `tests/crypto/test_keys.py`

**Interfaces:**
- Produces:
  - `AppleCertInfo` (Pydantic: `pass_type_identifier`, `team_identifier`, `organization_name`, `cert_serial`, `cert_fingerprint_sha256`, `not_before: datetime`, `not_after: datetime`, `nfc_capable: bool`, `issuer_generation: str`)
  - `parse_apple_certificate(pem: bytes) -> AppleCertInfo`
  - `certificate_matches_key(cert_pem: bytes, key_pem: bytes) -> bool`
  - `GoogleServiceAccountInfo` (Pydantic: `service_account_email`, `private_key_id`, `project_id`)
  - `parse_service_account(raw: bytes) -> GoogleServiceAccountInfo`
  - `generate_private_key() -> bytes` (RSA-2048 PEM, PKCS#8, unencrypted)
  - `build_csr(key_pem: bytes, common_name: str) -> bytes` (PEM)

- [ ] **Step 1: Write the failing certificate test**

```python
# tests/crypto/test_certificates.py
from pathlib import Path

from edutap.pass_builder.crypto.certificates import parse_apple_certificate

CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"


def test_apple_certificate_fields_are_extracted():
    info = parse_apple_certificate(CERT.read_bytes())
    assert info.pass_type_identifier == "pass.de.lmu.ub"
    assert info.team_identifier == "JG943677ZY"
    assert info.organization_name.startswith("Ludwig-Maximilians")
    assert info.issuer_generation == "G4"
    assert info.nfc_capable is True
    assert info.not_after.year == 2026
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/crypto/test_certificates.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `crypto/certificates.py`**

```python
"""Extract metadata from Apple certificates and Google service accounts."""

import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID
from cryptography.x509.oid import ObjectIdentifier
from pydantic import BaseModel

_NFC_EXTENSION = ObjectIdentifier("1.2.840.113635.100.6.1.26")


class AppleCertInfo(BaseModel):
    """Metadata derived from an Apple pass type certificate."""

    pass_type_identifier: str
    team_identifier: str
    organization_name: str
    cert_serial: str
    cert_fingerprint_sha256: str
    not_before: object
    not_after: object
    nfc_capable: bool
    issuer_generation: str


class GoogleServiceAccountInfo(BaseModel):
    """Metadata derived from a Google service account JSON file."""

    service_account_email: str
    private_key_id: str
    project_id: str


def _first(name: x509.Name, oid) -> str:
    values = name.get_attributes_for_oid(oid)
    return values[0].value if values else ""


def parse_apple_certificate(pem: bytes) -> AppleCertInfo:
    """Return the metadata of an Apple pass type certificate."""
    cert = x509.load_pem_x509_certificate(pem)
    nfc_capable = True
    try:
        cert.extensions.get_extension_for_oid(_NFC_EXTENSION)
    except x509.ExtensionNotFound:
        nfc_capable = False
    return AppleCertInfo(
        pass_type_identifier=_first(cert.subject, NameOID.USER_ID),
        team_identifier=_first(cert.subject, NameOID.ORGANIZATIONAL_UNIT_NAME),
        organization_name=_first(cert.subject, NameOID.ORGANIZATION_NAME),
        cert_serial=format(cert.serial_number, "X"),
        cert_fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        nfc_capable=nfc_capable,
        issuer_generation=_first(cert.issuer, NameOID.ORGANIZATIONAL_UNIT_NAME),
    )


def certificate_matches_key(cert_pem: bytes, key_pem: bytes) -> bool:
    """Return True if the certificate's public key matches the private key."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = load_pem_private_key(key_pem, password=None)
    cert_numbers = cert.public_key().public_numbers()
    key_numbers = key.public_key().public_numbers()
    return cert_numbers == key_numbers


def parse_service_account(raw: bytes) -> GoogleServiceAccountInfo:
    """Return the metadata of a Google service account JSON file."""
    data = json.loads(raw)
    return GoogleServiceAccountInfo(
        service_account_email=data["client_email"],
        private_key_id=data["private_key_id"],
        project_id=data["project_id"],
    )
```

Type the two datetime fields as `datetime` (import from `datetime`); they are shown as `object` above only to keep the snippet import-light.

- [ ] **Step 4: Run the certificate test**

Run: `uv run pytest tests/crypto/test_certificates.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing key/CSR test**

```python
# tests/crypto/test_keys.py
from cryptography import x509

from edutap.pass_builder.crypto.certificates import certificate_matches_key
from edutap.pass_builder.crypto.keys import build_csr
from edutap.pass_builder.crypto.keys import generate_private_key


def test_generated_key_is_rsa_2048_pem():
    key_pem = generate_private_key()
    assert b"BEGIN PRIVATE KEY" in key_pem


def test_csr_carries_the_common_name_and_matches_the_key():
    key_pem = generate_private_key()
    csr_pem = build_csr(key_pem, "Pass Type ID: pass.demo.lmu.de")
    csr = x509.load_pem_x509_csr(csr_pem)
    assert csr.is_signature_valid
    assert "pass.demo.lmu.de" in csr.subject.rfc4514_string()
```

- [ ] **Step 6: Run it and confirm it fails**

Run: `uv run pytest tests/crypto/test_keys.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Implement `crypto/keys.py`**

```python
"""Generate private keys and certificate signing requests."""

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509 import CertificateSigningRequestBuilder
from cryptography.x509 import Name
from cryptography.x509 import NameAttribute
from cryptography.x509.oid import NameOID


def generate_private_key() -> bytes:
    """Return a fresh unencrypted RSA-2048 private key in PKCS#8 PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def build_csr(key_pem: bytes, common_name: str) -> bytes:
    """Return a CSR in PEM for the given key and common name."""
    key = load_pem_private_key(key_pem, password=None)
    subject = Name([NameAttribute(NameOID.COMMON_NAME, common_name)])
    csr = CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM)
```

- [ ] **Step 8: Run the key test and full suite**

Run: `uv run pytest tests/crypto -v && make lint`
Expected: PASS, lint clean

- [ ] **Step 9: Commit**

```bash
git add src/edutap/pass_builder/crypto tests/crypto tests/fixtures/apple_cert.pem
git commit -m "feat: add certificate parsing, key generation and CSR building"
```

---

### Task 9: Secret backend (AES-GCM in the database)

**Files:**
- Create: `src/edutap/pass_builder/secrets/__init__.py`, `secrets/backend.py`, `secrets/dbcrypto.py`
- Test: `tests/secrets/test_dbcrypto.py`

**Interfaces:**
- Consumes: `SecretKind` enum from Task 3.
- Produces:
  - `SealedSecret` (Pydantic: `ciphertext: bytes`, `nonce: bytes`, `wrapped_dek: bytes`, `algo: str`)
  - `SecretBackend` (Protocol: `seal(plaintext: bytes) -> SealedSecret`, `open(sealed: SealedSecret) -> bytes`)
  - `DatabaseSecretBackend(master_key_b64: str)` implementing the protocol with envelope encryption: a fresh 32-byte DEK per secret, AES-256-GCM for the payload, the DEK wrapped with the master key via AES-256-GCM.

- [ ] **Step 1: Write the failing round-trip test**

```python
# tests/secrets/test_dbcrypto.py
import base64
import os

import pytest

from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend


def make_backend() -> DatabaseSecretBackend:
    return DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())


def test_seal_then_open_returns_the_plaintext():
    backend = make_backend()
    sealed = backend.seal(b"super-secret-key")
    assert backend.open(sealed) == b"super-secret-key"


def test_each_seal_uses_a_fresh_nonce_and_dek():
    backend = make_backend()
    a = backend.seal(b"same")
    b = backend.seal(b"same")
    assert a.ciphertext != b.ciphertext
    assert a.nonce != b.nonce
    assert a.wrapped_dek != b.wrapped_dek


def test_tampered_ciphertext_is_rejected():
    backend = make_backend()
    sealed = backend.seal(b"x")
    sealed.ciphertext = sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1])
    with pytest.raises(Exception):
        backend.open(sealed)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/secrets/test_dbcrypto.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `secrets/backend.py`**

```python
"""The secret backend protocol."""

from typing import Protocol

from pydantic import BaseModel


class SealedSecret(BaseModel):
    """An encrypted secret ready to be stored in the database."""

    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    algo: str = "AES-256-GCM"


class SecretBackend(Protocol):
    """Seals and opens secret material."""

    def seal(self, plaintext: bytes) -> SealedSecret:
        """Encrypt the plaintext."""
        ...

    def open(self, sealed: SealedSecret) -> bytes:
        """Decrypt a sealed secret."""
        ...
```

- [ ] **Step 4: Implement `secrets/dbcrypto.py`**

```python
"""Envelope encryption of secrets for storage in the database."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .backend import SealedSecret


class DatabaseSecretBackend:
    """Encrypt secrets with a per-secret data key wrapped by a master key."""

    def __init__(self, master_key_b64: str) -> None:
        master_key = base64.b64decode(master_key_b64)
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes (base64 encoded)")
        self._master = AESGCM(master_key)

    def seal(self, plaintext: bytes) -> SealedSecret:
        """Encrypt the plaintext under a fresh data key."""
        dek = os.urandom(32)
        nonce = os.urandom(12)
        wrap_nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, None)
        wrapped = wrap_nonce + self._master.encrypt(wrap_nonce, dek, None)
        return SealedSecret(ciphertext=ciphertext, nonce=nonce, wrapped_dek=wrapped)

    def open(self, sealed: SealedSecret) -> bytes:
        """Decrypt a sealed secret."""
        wrap_nonce, wrapped = sealed.wrapped_dek[:12], sealed.wrapped_dek[12:]
        dek = self._master.decrypt(wrap_nonce, wrapped, None)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, None)
```

- [ ] **Step 5: Run the round-trip tests**

Run: `uv run pytest tests/secrets/test_dbcrypto.py -v`
Expected: PASS — 3 tests

- [ ] **Step 6: Commit**

```bash
git add src/edutap/pass_builder/secrets tests/secrets
git commit -m "feat: add AES-GCM envelope secret backend"
```

---

### Task 10: data_provider client with projection

**Files:**
- Create: `src/edutap/pass_builder/clients/__init__.py`, `clients/data_provider.py`
- Test: `tests/clients/test_data_provider.py`

**Interfaces:**
- Consumes: `Settings` from Task 1; `ProblemError` from Task 1.
- Produces:
  - `CatalogueField` (Pydantic: `key`, `value_type`, `label`, `required`, `description`)
  - `DataProviderClient(base_url, token, timeout, client: httpx.AsyncClient)`
    - `async fetch_fields(person_uid: str, fields: list[str]) -> dict[str, Any]` — POSTs `{person_uid, fields}` to `/lookup`, returns the field map. Raises `ProblemError(502, "data_provider_unavailable")` on connect error (after one retry), never retries on 4xx.
    - `async fetch_catalogue() -> list[CatalogueField]` — GETs `/catalogue`.

- [ ] **Step 1: Write the failing tests with respx**

```python
# tests/clients/test_data_provider.py
import httpx
import pytest
import respx

from edutap.pass_builder.clients.data_provider import DataProviderClient
from edutap.pass_builder.errors import ProblemError


def make_client(http: httpx.AsyncClient) -> DataProviderClient:
    return DataProviderClient("http://dp", "", 5.0, http)


@respx.mock
async def test_fetch_fields_sends_projection_and_returns_map():
    route = respx.post("http://dp/lookup").mock(
        return_value=httpx.Response(200, json={"person.name": "Ada"})
    )
    async with httpx.AsyncClient() as http:
        result = await make_client(http).fetch_fields("u1", ["person.name"])
    assert result == {"person.name": "Ada"}
    assert route.calls.last.request.content == b'{"person_uid":"u1","fields":["person.name"]}'


@respx.mock
async def test_connection_error_becomes_502_problem():
    respx.post("http://dp/lookup").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_fields("u1", ["person.name"])
    assert excinfo.value.slug == "data_provider_unavailable"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/clients/test_data_provider.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `clients/data_provider.py`**

```python
"""HTTP client for edutap.data_provider with field projection."""

import json
from typing import Any

import httpx
from pydantic import BaseModel

from ..errors import ProblemError


class CatalogueField(BaseModel):
    """One field the data provider can deliver."""

    key: str
    value_type: str
    label: str | None = None
    required: bool = False
    description: str | None = None


class DataProviderClient:
    """Fetch projected person data and the field catalogue."""

    def __init__(self, base_url: str, token: str, timeout: float, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._client = client

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict[str, Any]:
        """Return exactly the requested fields for one person."""
        payload = json.dumps({"person_uid": person_uid, "fields": fields}).encode()
        for attempt in (1, 2):
            try:
                response = await self._client.post(
                    f"{self._base_url}/lookup",
                    content=payload,
                    headers={**self._headers, "content-type": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.ConnectError:
                if attempt == 2:
                    raise ProblemError(502, "data_provider_unavailable", "Data provider unavailable")
                continue
            if response.status_code >= 400:
                raise ProblemError(502, "data_provider_unavailable", "Data provider error")
            return response.json()
        raise ProblemError(502, "data_provider_unavailable", "Data provider unavailable")

    async def fetch_catalogue(self) -> list[CatalogueField]:
        """Return the field catalogue."""
        response = await self._client.get(f"{self._base_url}/catalogue", timeout=self._timeout)
        return [CatalogueField(**row) for row in response.json()]
```

- [ ] **Step 4: Run the client tests**

Run: `uv run pytest tests/clients/test_data_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/clients/__init__.py src/edutap/pass_builder/clients/data_provider.py tests/clients
git commit -m "feat: add data_provider client with field projection"
```

---

### Task 11: Object store client (RustFS)

**Files:**
- Create: `src/edutap/pass_builder/clients/objectstore.py`
- Test: `tests/clients/test_objectstore.py` (marked `integration`)

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `ObjectStore(endpoint_url, bucket, access_key, secret_key)` with `async put(key: str, data: bytes, content_type: str) -> None`, `async get(key: str) -> bytes`, `content_key(tenant, version_id, sha256) -> str` returning `f"{tenant}/{version_id}/{sha256}"`.

- [ ] **Step 1: Write the failing pure test for key derivation**

```python
# tests/clients/test_objectstore.py
from edutap.pass_builder.clients.objectstore import ObjectStore


def test_content_key_is_tenant_version_sha():
    assert ObjectStore.content_key("lmu", "v1", "abc") == "lmu/v1/abc"
```

Add a second test marked `@pytest.mark.integration` that puts and gets a blob against the compose `objectstore` service.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/clients/test_objectstore.py::test_content_key_is_tenant_version_sha -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `clients/objectstore.py`**

```python
"""S3-compatible object store client for RustFS."""

import aioboto3


class ObjectStore:
    """Store and retrieve content-addressed template assets."""

    def __init__(self, endpoint_url: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._endpoint_url = endpoint_url
        self._bucket = bucket
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        """Return the content-addressed object key."""
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store a blob."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        """Retrieve a blob."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            return await response["Body"].read()
```

- [ ] **Step 4: Run the key-derivation test**

Run: `uv run pytest tests/clients/test_objectstore.py::test_content_key_is_tenant_version_sha -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/clients/objectstore.py tests/clients/test_objectstore.py
git commit -m "feat: add RustFS object store client"
```

---

### Task 12: API-client authentication and scope dependency

**Files:**
- Create: `src/edutap/pass_builder/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `ApiClient` model, `Scope` enum from Task 3; `get_session` from Task 3; `ProblemError` from Task 1.
- Produces:
  - `AuthContext` (Pydantic: `client_id: UUID`, `tenant_id: UUID`, `scopes: set[Scope]`)
  - `hash_token(token: str) -> str` (SHA-256 hex)
  - `require(*scopes: Scope)` returning a FastAPI dependency that resolves the bearer token to an `AuthContext`, raising `ProblemError(401, "unauthenticated")` for an unknown/inactive token and `ProblemError(403, "insufficient_scope")` when a required scope is missing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.auth import hash_token
from edutap.pass_builder.auth import resolve_token
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import ApiClient
from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.models.enums import Scope


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


async def seed(session) -> str:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    session.add(ApiClient(
        tenant_id=tenant.id, name="mgr",
        token_hash=hash_token("secret-token"),
        scopes=[Scope.MANAGE], active=True,
    ))
    await session.flush()
    return "secret-token"


async def test_valid_token_resolves_to_context(session):
    token = await seed(session)
    context = await resolve_token(session, token)
    assert isinstance(context, AuthContext)
    assert Scope.MANAGE in context.scopes


async def test_unknown_token_is_unauthenticated(session):
    with pytest.raises(ProblemError) as excinfo:
        await resolve_token(session, "nope")
    assert excinfo.value.status == 401
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `auth.py`**

```python
"""API client authentication and scope enforcement."""

import hashlib
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from fastapi import Depends
from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .errors import ProblemError
from .models.db import ApiClient
from .models.enums import Scope


class AuthContext(BaseModel):
    """The authenticated caller."""

    client_id: UUID
    tenant_id: UUID
    scopes: set[Scope]


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of a bearer token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def resolve_token(session: AsyncSession, token: str) -> AuthContext:
    """Resolve a bearer token to its auth context."""
    row = (
        await session.execute(
            select(ApiClient).where(
                ApiClient.token_hash == hash_token(token),
                ApiClient.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProblemError(401, "unauthenticated", "Unknown or inactive token")
    return AuthContext(client_id=row.id, tenant_id=row.tenant_id, scopes=set(row.scopes))


def require(*scopes: Scope) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Return a dependency enforcing the given scopes."""

    async def dependency(
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> AuthContext:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise ProblemError(401, "unauthenticated", "Missing bearer token")
        context = await resolve_token(session, header.removeprefix("Bearer "))
        if not set(scopes).issubset(context.scopes):
            raise ProblemError(403, "insufficient_scope", "Missing required scope")
        return context

    return dependency
```

- [ ] **Step 4: Run the auth tests**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/auth.py tests/test_auth.py
git commit -m "feat: add API client authentication and scope dependency"
```

---

### Task 13: Credential service and lifecycle

**Files:**
- Create: `src/edutap/pass_builder/services/__init__.py`, `services/credentials.py`
- Test: `tests/services/test_credentials.py`

**Interfaces:**
- Consumes: `CredentialSet`, `SecretBlob` models, enums from Task 3; crypto from Task 8; `SecretBackend` from Task 9; `ProblemError` from Task 1.
- Produces: `CredentialService(session, backend)` with:
  - `async create_apple(tenant_id, label, common_name) -> CredentialSet` — generates a key, seals it, stores a `SecretBlob`, builds and stores the CSR, status `key_pending`.
  - `async install_certificate(tenant_id, credential_id, cert_pem) -> CredentialSet` — verifies `certificate_matches_key`, else `ProblemError(409, "certificate_key_mismatch")`; extracts metadata; status `active`.
  - `async import_apple(tenant_id, label, key_pem, cert_pem) -> CredentialSet`
  - `async import_google(tenant_id, label, service_account_raw, issuer_id) -> CredentialSet`
  - `async list_sets(tenant_id, provider=None, expiring_within_days=None) -> list[CredentialSet]`
  - `async get_csr(tenant_id, credential_id) -> str`
  - `async open_material(credential_set) -> bytes` — decrypts the stored secret for the render path.

- [ ] **Step 1: Write the failing lifecycle test**

```python
# tests/services/test_credentials.py
import base64
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.models.enums import CredentialStatus
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService

CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


def service(session) -> CredentialService:
    backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())
    return CredentialService(session, backend)


async def a_tenant(session) -> Tenant:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    return tenant


async def test_create_apple_yields_key_pending_with_csr(session):
    tenant = await a_tenant(session)
    svc = service(session)
    cred = await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")
    assert cred.status == CredentialStatus.KEY_PENDING
    assert "BEGIN CERTIFICATE REQUEST" in cred.csr_pem


async def test_install_mismatched_certificate_is_rejected(session):
    tenant = await a_tenant(session)
    svc = service(session)
    cred = await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")
    with pytest.raises(ProblemError) as excinfo:
        await svc.install_certificate(tenant.id, cred.id, CERT.read_bytes())
    assert excinfo.value.slug == "certificate_key_mismatch"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/services/test_credentials.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `services/credentials.py`**

Implement `CredentialService` per the interface. Key points the tests pin down: `create_apple` calls `generate_private_key()`, `build_csr(key, common_name)`, seals the key via the backend, writes a `SecretBlob(kind=PRIVATE_KEY)` and a `CredentialSet(status=KEY_PENDING, csr_pem=…)`. `install_certificate` loads the stored key material, calls `certificate_matches_key`, raises `ProblemError(409, "certificate_key_mismatch")` on mismatch, otherwise fills the `AppleCertInfo` fields and sets `status=ACTIVE`. `list_sets` with `expiring_within_days` filters on `not_after <= now + delta`. All writes flush within the caller's transaction. Full implementation code is written during execution following these exact signatures.

- [ ] **Step 4: Run the credential tests**

Run: `uv run pytest tests/services/test_credentials.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/services/__init__.py src/edutap/pass_builder/services/credentials.py tests/services/test_credentials.py
git commit -m "feat: add credential service with Apple key/CSR/certificate lifecycle"
```

---

### Task 14: Template service — lifecycle, import, mapping validation, publish

**Files:**
- Create: `src/edutap/pass_builder/services/templates.py`
- Create: `src/edutap/pass_builder/services/mapping_validation.py`
- Test: `tests/services/test_templates.py`, `tests/services/test_mapping_validation.py`

**Interfaces:**
- Consumes: template models and enums from Task 3; `ObjectStore` from Task 11; `scan_placeholders` from Task 5; `DataField` from Task 3; `ProblemError` from Task 1.
- Produces:
  - `validate_mapping_rules(rules: list[RuleSpec], catalogue: dict[str, str]) -> list[str]` returning a list of problem strings (unknown field, type mismatch); empty means valid.
  - `TemplateService(session, objectstore)` with:
    - `async import_apple_version(tenant_id, variant_id, bundle: bytes) -> TemplateVersion` — unzips, splits `pass.json` from assets, strips `tooling.json`, stores assets content-addressed, stores the original bundle, status `draft`.
    - `async set_mappings(tenant_id, version_id, rules: list[RuleSpec]) -> None` — draft only, else `ProblemError(409, "version_not_draft")`; validates against the `data_field` cache.
    - `async publish(tenant_id, version_id) -> TemplateVersion` — runs full validation (collect all findings → `ProblemError(422, "template_validation_failed", findings=[…])`); for Google derives `mapping_rule` rows from `scan_placeholders(object_json)`; sets `published`, archives the previous published version.
    - `async build_render_spec(tenant_id, template_key, wallet_type, variant_key, version_number) -> RenderSpec` — resolves the version (default variant, published unless pinned), loads assets from the object store.

- [ ] **Step 1: Write the failing mapping-validation tests**

```python
# tests/services/test_mapping_validation.py
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType
from edutap.pass_builder.services.mapping_validation import validate_mapping_rules


def rule(source_field, value_type=ValueType.TEXT):
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE, target="name",
        source_field=source_field, value_type=value_type,
    )


def test_unknown_field_is_reported():
    problems = validate_mapping_rules([rule("person.unknown")], {"person.name": "text"})
    assert any("person.unknown" in p for p in problems)


def test_type_mismatch_is_reported():
    problems = validate_mapping_rules(
        [rule("person.name", ValueType.DATE)], {"person.name": "text"}
    )
    assert any("person.name" in p and "type" in p.lower() for p in problems)


def test_valid_rule_yields_no_problems():
    assert validate_mapping_rules([rule("person.name")], {"person.name": "text"}) == []
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/services/test_mapping_validation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `services/mapping_validation.py`**

```python
"""Validate mapping rules against the cached data-provider catalogue."""

from ..engine.spec import RuleSpec


def validate_mapping_rules(rules: list[RuleSpec], catalogue: dict[str, str]) -> list[str]:
    """Return a list of problems; an empty list means the rule set is valid."""
    problems: list[str] = []
    for rule in rules:
        known_type = catalogue.get(rule.source_field)
        if known_type is None:
            problems.append(f"unknown field: {rule.source_field}")
            continue
        if known_type != rule.value_type.value:
            problems.append(
                f"type mismatch for {rule.source_field}: "
                f"catalogue says {known_type}, rule says {rule.value_type.value}"
            )
    return problems
```

- [ ] **Step 4: Run the validation tests**

Run: `uv run pytest tests/services/test_mapping_validation.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing Apple-import test**

```python
# tests/services/test_templates.py  (excerpt)
import io
import zipfile

# build an in-memory .pkpasstemplate: pass.json + icon.png + tooling.json
def make_bundle() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pass.json", '{"formatVersion":1,"generic":{}}')
        zf.writestr("icon.png", b"\x89PNG")
        zf.writestr("tooling.json", '{"designerVersion":"1"}')
    return buffer.getvalue()


async def test_apple_import_splits_pass_json_and_strips_tooling(session, objectstore, tenant_variant):
    svc = TemplateService(session, objectstore)
    version = await svc.import_apple_version(tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle())
    assert version.pass_json["formatVersion"] == 1
    filenames = {a.filename for a in await svc.list_assets(version.id)}
    assert "icon.png" in filenames
    assert "tooling.json" not in filenames
```

Provide `objectstore` and `tenant_variant` fixtures in this test module (an in-memory fake object store recording `put` calls suffices for the unit level).

- [ ] **Step 6: Run it and confirm it fails**

Run: `uv run pytest tests/services/test_templates.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Implement `services/templates.py`**

Implement `TemplateService` per the interface. `import_apple_version` uses `zipfile.ZipFile` over the bytes, reads `pass.json` into the column, iterates the remaining names, skips `tooling.json`, computes each asset's `sha256`, calls `objectstore.put(content_key(...), data, media_type)` and writes a `TemplateAsset` row, stores the untouched bundle under `source_object_key`. `publish` loads the `data_field` cache into a `{key: value_type}` dict, runs `validate_mapping_rules`, adds template-structural checks (every `field_value`/`field_label` target exists among the pass field keys; `icon.png` present; if `nfc_enabled` the variant's credential set is `nfc_capable`), collects all findings, and on any finding raises `ProblemError(422, "template_validation_failed", findings=problems)`. For Google it derives rules via `scan_placeholders`. Full body written during execution against these signatures and the tests.

- [ ] **Step 8: Run the template tests**

Run: `uv run pytest tests/services/test_templates.py -v`
Expected: PASS

- [ ] **Step 9: Run lint and full local suite**

Run: `make lint && make test-local`
Expected: all green

- [ ] **Step 10: Commit**

```bash
git add src/edutap/pass_builder/services/templates.py src/edutap/pass_builder/services/mapping_validation.py tests/services/test_templates.py tests/services/test_mapping_validation.py
git commit -m "feat: add template service with import, mapping validation and publish"
```

---

### Task 15: Engine build — Apple (`.pkpass` bytes)

**Files:**
- Create: `src/edutap/pass_builder/engine/apple_build.py`
- Test: `tests/engine/test_apple_build.py` (marked `integration` for the signing part; the assembly part is a unit test with a stubbed signer)

**Interfaces:**
- Consumes: `apply_apple` from Task 6; `RenderSpec`, `BoundValue` from Task 4; `edutap.wallet_apple.api`.
- Produces: `build_apple(spec: RenderSpec, bound: list[BoundValue], serial_number: str, sign) -> bytes` where `sign` is a callable `(PkPass) -> None` (injected so the unit test can stub signing). It sets `pass_json["serialNumber"] = serial_number`, applies NFC config (`nfc_enabled` → `pass_json["nfc"]` with `encryptionPublicKey`/`requiresAuthentication`), calls `apply_apple`, constructs a `PkPass` from the pass dict and assets, invokes `sign`, and returns `api.pkpass(pkpass)` bytes.

- [ ] **Step 1: Write the failing assembly test (stubbed signer)**

```python
# tests/engine/test_apple_build.py
from edutap.pass_builder.engine.apple_build import build_apple
from edutap.pass_builder.engine.spec import BoundValue
from edutap.pass_builder.engine.spec import RenderSpec
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType
from edutap.pass_builder.models.enums import WalletType


def test_serial_number_is_set_and_bytes_returned():
    spec = RenderSpec(
        wallet_type=WalletType.APPLE,
        pass_json={"formatVersion": 1, "generic": {"primaryFields": [
            {"key": "name", "label": "Name", "value": ""}]}},
        assets={"icon.png": b"\x89PNG"},
    )
    bound = [BoundValue(
        rule=RuleSpec(target_kind=TargetKind.FIELD_VALUE, target="name",
                      source_field="person.name", value_type=ValueType.TEXT),
        value="Ada")]
    captured = {}

    def fake_sign(pkpass):
        captured["serial"] = pkpass.pass_object.serialNumber

    result = build_apple(spec, bound, "serial-123", fake_sign)
    assert isinstance(result, bytes)
    assert captured["serial"] == "serial-123"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/engine/test_apple_build.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `engine/apple_build.py`**

```python
"""Assemble and sign an Apple .pkpass from a render spec."""

from collections.abc import Callable

from edutap.wallet_apple import api

from .apple_apply import apply_apple
from .spec import BoundValue
from .spec import RenderSpec


def build_apple(
    spec: RenderSpec,
    bound: list[BoundValue],
    serial_number: str,
    sign: Callable[[object], None],
) -> bytes:
    """Return signed .pkpass bytes for the given spec and bound values."""
    pass_json = dict(spec.pass_json or {})
    pass_json["serialNumber"] = serial_number
    if spec.nfc_enabled:
        nfc = pass_json.setdefault("nfc", {})
        if spec.nfc_encryption_public_key:
            nfc["encryptionPublicKey"] = spec.nfc_encryption_public_key
        nfc["requiresAuthentication"] = spec.nfc_requires_authentication
    pass_json, assets = apply_apple(pass_json, dict(spec.assets), bound)
    pkpass = api.new(data=pass_json)
    for filename, data in assets.items():
        pkpass.files[filename] = data
    sign(pkpass)
    return api.pkpass(pkpass).read()
```

If `api.new` in the installed version does not accept a pre-built dict cleanly, replace the construction with `api.from_template` over the stored original bundle; the injected `sign` and the return line stay identical. Confirm against the installed `wallet_apple` at execution time.

- [ ] **Step 4: Run the assembly test**

Run: `uv run pytest tests/engine/test_apple_build.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/engine/apple_build.py tests/engine/test_apple_build.py
git commit -m "feat: add Apple pkpass assembly and signing"
```

---

### Task 16: Engine build — Google (object push and save link)

**Files:**
- Create: `src/edutap/pass_builder/engine/google_build.py`
- Test: `tests/engine/test_google_build.py`

**Interfaces:**
- Consumes: `apply_google` from Task 7; `RenderSpec`, `BoundValue` from Task 4; `edutap.wallet_google.api`.
- Produces:
  - `google_object_id(issuer_id: str, pass_uuid: str) -> str` returning `f"{issuer_id}.{pass_uuid}"`
  - `build_google_object(spec, bound, object_id, class_id) -> dict` — resolves placeholders, sets `id`/`classId`, returns the object dict ready for `api.create`/`api.update`. Pushing to Google and building the save link live in the service layer (Task 17) so the engine stays free of network and credentials.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_google_build.py
from edutap.pass_builder.engine.google_build import build_google_object
from edutap.pass_builder.engine.google_build import google_object_id
from edutap.pass_builder.engine.spec import BoundValue
from edutap.pass_builder.engine.spec import RenderSpec
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind
from edutap.pass_builder.models.enums import ValueType
from edutap.pass_builder.models.enums import WalletType


def test_object_id_is_issuer_dot_uuid():
    assert google_object_id("3388", "abc-uuid") == "3388.abc-uuid"


def test_object_carries_id_class_and_resolved_values():
    spec = RenderSpec(
        wallet_type=WalletType.GOOGLE,
        object_json={"cardTitle": {"defaultValue": {"value": "${person.name}"}}},
    )
    bound = [BoundValue(
        rule=RuleSpec(target_kind=TargetKind.JSON_POINTER, target="/x",
                      source_field="person.name", value_type=ValueType.TEXT),
        value="Ada")]
    obj = build_google_object(spec, bound, "3388.abc", "3388.student")
    assert obj["id"] == "3388.abc"
    assert obj["classId"] == "3388.student"
    assert obj["cardTitle"]["defaultValue"]["value"] == "Ada"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/engine/test_google_build.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `engine/google_build.py`**

```python
"""Assemble a Google wallet object from a render spec."""

from .google_apply import apply_google
from .spec import BoundValue
from .spec import RenderSpec


def google_object_id(issuer_id: str, pass_uuid: str) -> str:
    """Return the stable object id, independent of template and variant."""
    return f"{issuer_id}.{pass_uuid}"


def build_google_object(
    spec: RenderSpec,
    bound: list[BoundValue],
    object_id: str,
    class_id: str,
) -> dict:
    """Return the wallet object with placeholders resolved and ids set."""
    obj = apply_google(dict(spec.object_json or {}), bound)
    obj["id"] = object_id
    obj["classId"] = class_id
    return obj
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/engine/test_google_build.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/engine/google_build.py tests/engine/test_google_build.py
git commit -m "feat: add Google wallet object assembly"
```

---

### Task 17: Render service and audit writing

**Files:**
- Create: `src/edutap/pass_builder/services/render.py`
- Create: `src/edutap/pass_builder/services/audit.py`
- Test: `tests/services/test_render.py`

**Interfaces:**
- Consumes: `TemplateService.build_render_spec` (Task 14); `bind`, `required_fields` (Task 4); `build_apple` (Task 15); `build_google_object`, `google_object_id` (Task 16); `CredentialService.open_material` (Task 13); `DataProviderClient` (Task 10); `edutap.wallet_apple.api.sign`/`sign_direct`, `edutap.wallet_google.api.create`/`update`/`save_link`.
- Produces:
  - `RenderResult` (Pydantic: `wallet_type`, `pkpass: bytes | None`, `object_id: str | None`, `class_id: str | None`, `template_version: int`, `variant: str`)
  - `write_audit(session, *, tenant_id, request_id, actor_client_id, action, outcome, error_code, duration_ms, template_id, variant_id, version_id, wallet_type, subject_ref, requested_fields) -> None`
  - `RenderService(session, templates, credentials, data_provider)` with:
    - `async create_pass(auth, *, pass_id, template_key, wallet_type, variant_key, person_uid, version_number) -> RenderResult`
    - `async update_pass(...)` (same, Google uses `update`)
    - `async save_link(auth, pass_id, ...) -> str`
    - `async preview(auth, *, template_key, wallet_type, variant_key, version_number, sample_data) -> dict`

- [ ] **Step 1: Write the failing render tests**

```python
# tests/services/test_render.py  (excerpt)
async def test_create_apple_pass_requests_only_mapped_fields(render_env):
    env = render_env  # provides tenant, published apple template with one rule person.name
    result = await env.service.create_pass(
        env.auth, pass_id="11111111-1111-1111-1111-111111111111",
        template_key="student-id", wallet_type=WalletType.APPLE,
        variant_key=None, person_uid="u1", version_number=None,
    )
    assert result.pkpass is not None
    assert env.data_provider.last_fields == ["person.name"]


async def test_missing_field_writes_error_audit_and_raises(render_env):
    env = render_env
    env.data_provider.response = {}  # person.name missing
    with pytest.raises(ProblemError) as excinfo:
        await env.service.create_pass(
            env.auth, pass_id="1", template_key="student-id",
            wallet_type=WalletType.APPLE, variant_key=None,
            person_uid="u1", version_number=None,
        )
    assert excinfo.value.slug == "missing_field"
    entries = await env.list_audit()
    assert entries[-1].outcome == "error"
    assert entries[-1].error_code == "missing_field"
    assert "person.name" not in str(entries[-1].details)  # no field values leak
```

The `render_env` fixture wires an in-memory fake `DataProviderClient` (records `last_fields`, returns `response`), a real `TemplateService`/`CredentialService` against `session` and a fake object store, and seeds one published Apple template whose single rule maps `person.name` to a field. Signing is stubbed through `CredentialService.open_material` returning a self-signed test key, or by injecting a no-op signer.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/services/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `services/audit.py`**

```python
"""Write audit entries. Never records field values or secrets."""

from datetime import UTC
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    request_id: str,
    actor_client_id: UUID,
    action: str,
    outcome: str,
    error_code: str | None,
    duration_ms: int,
    template_id: UUID | None,
    variant_id: UUID | None,
    version_id: UUID | None,
    wallet_type: str | None,
    subject_ref: str | None,
    requested_fields: list[str],
) -> None:
    """Persist one audit entry within the caller's transaction."""
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            ts=datetime.now(UTC),
            request_id=request_id,
            actor_client_id=actor_client_id,
            action=action,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=subject_ref,
            requested_fields=requested_fields,
            details={},
        )
    )
```

- [ ] **Step 4: Implement `services/render.py`**

Implement `RenderService` per the interface. Flow of `create_pass`: `build_render_spec` → `required_fields(spec.rules)` → `data_provider.fetch_fields(person_uid, fields)` → `bind(spec.rules, data)` → for Apple `build_apple(spec, bound, serial_number=pass_id, sign=<signer from credentials>)`; for Google `build_google_object(...)` then `api.create(...)`. On success and on every caught `ProblemError`, call `write_audit` (success/error) with `requested_fields`, then re-raise on error. `preview` skips the data provider, fills placeholders from `sample_data` plus generated placeholders, and writes no audit. Google 409 "already exists" on `create` is treated as success. Full body written during execution against these signatures and the tests.

- [ ] **Step 5: Run the render tests**

Run: `uv run pytest tests/services/test_render.py -v`
Expected: PASS

- [ ] **Step 6: Run lint and full local suite**

Run: `make lint && make test-local`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add src/edutap/pass_builder/services/render.py src/edutap/pass_builder/services/audit.py tests/services/test_render.py
git commit -m "feat: add render service with projection and audit writing"
```

---

### Task 18: API schemas and dependency wiring

**Files:**
- Create: `src/edutap/pass_builder/models/api.py`
- Create: `src/edutap/pass_builder/dependencies.py`
- Test: `tests/test_api_schemas.py`

**Interfaces:**
- Produces:
  - Request/response Pydantic models: `CreatePassRequest`, `GooglePassResponse`, `CreateTemplateRequest`/`Response`, `CreateVariantRequest`/`Response`, `MappingRulesRequest` (list of `RuleSpec`), `CreateCredentialRequest`, `CredentialResponse` (**no secret fields**), `PreviewRequest`, `FieldResponse`, `AuditEntryResponse`.
  - `dependencies.py`: `get_data_provider()`, `get_objectstore()`, `get_secret_backend()`, `get_template_service()`, `get_credential_service()`, `get_render_service()` — FastAPI dependencies constructed from `Settings` and the request-scoped `session`, plus the shared `httpx.AsyncClient` from `app.state`.

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_api_schemas.py
from edutap.pass_builder.models.api import CredentialResponse


def test_credential_response_has_no_secret_fields():
    fields = set(CredentialResponse.model_fields)
    for forbidden in ("private_key", "service_account_json", "ciphertext", "wrapped_dek"):
        assert forbidden not in fields
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_api_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `models/api.py` and `dependencies.py`**

Define the schemas listed in the interface. `CredentialResponse` exposes only metadata: `id`, `provider`, `label`, `status`, `pass_type_identifier`, `team_identifier`, `organization_name`, `not_before`, `not_after`, `nfc_capable`, `service_account_email`, `issuer_id`, `cert_fingerprint_sha256`. `dependencies.py` builds each service from `get_settings()` and `Depends(get_session)`; the `httpx.AsyncClient` is read from `request.app.state.http`.

- [ ] **Step 4: Run the schema test**

Run: `uv run pytest tests/test_api_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/models/api.py src/edutap/pass_builder/dependencies.py tests/test_api_schemas.py
git commit -m "feat: add API schemas and dependency wiring"
```

---

### Task 19: Routers — passes, templates, credentials, fields, audit; lifespan and readiness

**Files:**
- Create: `routers/passes.py`, `routers/templates.py`, `routers/credentials.py`, `routers/fields.py`, `routers/audit.py`
- Modify: `routers/health.py` (add `/readyz`), `app.py` (include routers, `lifespan` with shared `httpx.AsyncClient`)
- Test: `tests/routers/test_passes.py`, `tests/routers/test_templates.py`, `tests/routers/test_credentials.py`, `tests/routers/test_tenant_isolation.py`, `tests/routers/test_no_secret_leak.py`

**Interfaces:**
- Consumes: all services, `require(*scopes)` from Task 12, schemas from Task 18.
- Produces: routers mounted under `/api/v1` with the endpoints from spec section 5. `create_app` overrides `get_session` in tests via `app.dependency_overrides`.

- [ ] **Step 1: Write the three mandatory cross-cutting tests (failing)**

```python
# tests/routers/test_tenant_isolation.py  (excerpt)
@pytest.mark.parametrize("path", [
    "/api/v1/templates/{id}",
    "/api/v1/variants/{id}",
    "/api/v1/versions/{id}",
])
async def test_other_tenant_gets_404(client_factory, path):
    owner, other = await two_tenants_with_template()
    response = await other.client.get(path.format(id=owner.template_id))
    assert response.status_code == 404
```

```python
# tests/routers/test_no_secret_leak.py  (excerpt)
async def test_no_endpoint_returns_key_material(credentials_client, known_private_key_pem):
    cred = await create_apple_credential(credentials_client)
    for path in (f"/api/v1/credentials", f"/api/v1/credentials/{cred['id']}/csr"):
        body = (await credentials_client.get(path)).text
        assert "PRIVATE KEY" not in body
        assert known_private_key_pem.decode() not in body
```

```python
# tests/routers/test_templates.py  (excerpt — immutability)
async def test_modifying_published_version_is_409(manage_client, published_version_id):
    response = await manage_client.put(
        f"/api/v1/versions/{published_version_id}/mappings", json={"rules": []})
    assert response.status_code == 409
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/routers -v`
Expected: FAIL — routers not implemented

- [ ] **Step 3: Implement the routers**

One `APIRouter(prefix="/api/v1")` per module, each endpoint declaring `auth: AuthContext = Depends(require(Scope.X))` and passing `auth.tenant_id` to the service so every query is tenant-scoped. Apple pass responses use `Response(content=bytes, media_type="application/vnd.apple.pkpass")` with `X-` metadata headers; Google returns `GooglePassResponse` with `201`. Version creation for Apple is `UploadFile` (multipart); for Google JSON. Not-found for another tenant's object returns `404` (never `403`).

- [ ] **Step 4: Implement `/readyz` and `lifespan`**

```python
# routers/health.py  — add
@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """Report readiness after checking database, object store and data provider."""
    checks = await run_readiness_checks(request.app.state)
    if not all(checks.values()):
        raise ProblemError(503, "not_ready", "Dependencies unavailable", checks=checks)
    return {"status": "ready"}
```

```python
# app.py — lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as http:
        app.state.http = http
        yield
```

Wire `lifespan=lifespan` into `create_app` and `app.include_router` for all six routers.

- [ ] **Step 5: Run the router tests**

Run: `uv run pytest tests/routers -v`
Expected: PASS

- [ ] **Step 6: Run lint and full local suite**

Run: `make lint && make test-local`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add src/edutap/pass_builder/routers src/edutap/pass_builder/app.py tests/routers
git commit -m "feat: add REST routers, readiness check and lifespan"
```

---

### Task 20: Audit retention sweep and field-catalogue refresh

**Files:**
- Create: `src/edutap/pass_builder/services/retention.py`
- Modify: `routers/fields.py` (add `POST /fields/refresh`)
- Test: `tests/services/test_retention.py`

**Interfaces:**
- Produces: `async purge_expired_audit(session, retention_months: int, now: datetime) -> int` deleting `audit_log` rows older than the cutoff and returning the count; `async refresh_catalogue(session, data_provider) -> int` replacing the `data_field` cache from `data_provider.fetch_catalogue()`.

- [ ] **Step 1: Write the failing retention test**

```python
# tests/services/test_retention.py
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from edutap.pass_builder.services.retention import purge_expired_audit


async def test_entries_older_than_retention_are_deleted(session, seed_audit):
    now = datetime(2026, 7, 21, tzinfo=UTC)
    await seed_audit(ts=now - timedelta(days=800))  # older than 24 months
    await seed_audit(ts=now - timedelta(days=10))   # recent
    deleted = await purge_expired_audit(session, retention_months=24, now=now)
    assert deleted == 1
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/services/test_retention.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `services/retention.py`**

```python
"""Audit retention and field-catalogue refresh."""

from datetime import datetime
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import AuditLog


async def purge_expired_audit(
    session: AsyncSession, retention_months: int, now: datetime
) -> int:
    """Delete audit entries older than the retention window; return the count."""
    cutoff = now - timedelta(days=retention_months * 30)
    result = await session.execute(delete(AuditLog).where(AuditLog.ts < cutoff))
    return result.rowcount or 0
```

Add `refresh_catalogue` replacing `data_field` rows from the catalogue in the same module.

- [ ] **Step 4: Run the retention test**

Run: `uv run pytest tests/services/test_retention.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/edutap/pass_builder/services/retention.py src/edutap/pass_builder/routers/fields.py tests/services/test_retention.py
git commit -m "feat: add audit retention sweep and catalogue refresh"
```

---

### Task 21: End-to-end integration test (real signing)

**Files:**
- Create: `tests/integration/test_end_to_end.py` (marked `integration`)
- Create: `tests/fixtures/test_signing_key.pem`, `tests/fixtures/test_signing_cert.pem`, `tests/fixtures/wwdr-g4.pem` (a self-signed test pass-type certificate and a test WWDR, generated by a helper script committed as `tests/fixtures/make_test_certs.py`)

**Interfaces:**
- Consumes: the running compose stack (`make -C . test-integration` after `docker compose up -d db objectstore`), a fake data provider served with `respx` or a stub app.

- [ ] **Step 1: Write the failing end-to-end test**

```python
# tests/integration/test_end_to_end.py
import pytest
from edutap.wallet_apple import api

pytestmark = pytest.mark.integration


async def test_apple_pass_is_built_signed_and_verifies(e2e_env):
    result = await e2e_env.create_apple_pass(person_uid="u1")
    pkpass = api.new(file=io.BytesIO(result.pkpass))
    api.verify(pkpass, settings=e2e_env.settings)  # raises if the signature is broken
```

`e2e_env` runs migrations against the compose database, seeds a tenant, an imported Apple credential using the committed test key and certificate, a published template with a `person.name` rule and an `icon.png` asset, and a fake data provider returning `{"person.name": "Ada"}`.

- [ ] **Step 2: Run it and confirm it fails**

Run: `docker compose up -d db objectstore && uv run pytest tests/integration -v`
Expected: FAIL — fixtures/wiring missing

- [ ] **Step 3: Implement the fixtures and helper**

Write `tests/fixtures/make_test_certs.py` generating a self-signed pass-type certificate (subject `UID=pass.test.local`, `OU=TEST123`, the NFC extension present) and a self-signed WWDR-substitute, saving all three PEM files. Wire `e2e_env` in `tests/integration/conftest.py`.

- [ ] **Step 4: Run the end-to-end test**

Run: `uv run pytest tests/integration -v`
Expected: PASS — the `.pkpass` verifies

- [ ] **Step 5: Commit**

```bash
git add tests/integration tests/fixtures/make_test_certs.py tests/fixtures/*.pem
git commit -m "test: add end-to-end integration test with real signing"
```

---

### Task 22: Upstream the Google placeholder resolver to edutap.wallet_google

**Files:**
- In `edutap.wallet_google`: create `src/edutap/wallet_google/placeholders.py` (copied verbatim from Task 5), export via `api.py`, add tests.
- In `edutap.pass_builder`: modify `engine/google_apply.py` to import from `edutap.wallet_google` when available, falling back to the local module.

**Interfaces:**
- Produces: `edutap.wallet_google.api.resolve_placeholders` / `scan_placeholders` with the same signatures as Task 5.

**Note:** This task resolves the flagged spec deviation. It is sequenced last so the builder is fully working first and this becomes a pure move. It requires a merged release of `wallet_google`; until then the local module stays authoritative.

- [ ] **Step 1: Copy the module and tests into `wallet_google`, open a PR there**

Follow that repository's contribution flow (test-first, its Makefile, its CHANGES). This is a cross-repo change; do not block the builder on it.

- [ ] **Step 2: Switch the builder import with a fallback**

```python
# engine/google_apply.py
try:
    from edutap.wallet_google.placeholders import resolve_placeholders
except ImportError:
    from .placeholders import resolve_placeholders
```

- [ ] **Step 3: Run the engine tests**

Run: `uv run pytest tests/engine/test_google_apply.py tests/engine/test_placeholders.py -v`
Expected: PASS with either import path

- [ ] **Step 4: Commit**

```bash
git add src/edutap/pass_builder/engine/google_apply.py
git commit -m "refactor: prefer wallet_google placeholder resolver when available"
```

---

### Task 23: Documentation and CI

**Files:**
- Create: `docs/` (Sphinx + MyST, Diataxis layout), `docs/conf.py`, `docs/index.md`, `docs/tutorials/`, `docs/how-to/`, `docs/reference/`, `docs/explanation/`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:** none (documentation and automation).

- [ ] **Step 1: Write the CI workflow**

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.14
      - run: uv pip install --system -e ".[dev,docs]"
      - run: uv run ruff check src tests
      - run: uv run ruff format --check src tests
      - run: uv run ty check
      - run: uv run pytest -m "not integration"
  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t pass-builder:ci .
```

- [ ] **Step 2: Write the documentation**

Author, following the `plone-doc-style` skill and Diataxis: a tutorial (build a first pass end to end), how-tos (obtain and install an Apple credential, import a `.pkpasstemplate`, run the Docker test environment), reference (the REST API and the data model), and an explanation (why stateless, why placeholders, why immutable versions). The Docker how-to documents build, up, running tests and teardown.

- [ ] **Step 3: Build the docs and verify CI locally**

Run: `uv run sphinx-build -b html docs docs/_build/html && make lint && make test-local`
Expected: docs build without warnings, lint and tests green

- [ ] **Step 4: Commit**

```bash
git add docs .github/workflows/ci.yml README.md
git commit -m "docs: add Sphinx documentation and CI workflow"
```

---

## Self-Review Notes

- **Spec coverage:** every section of the spec maps to a task — tenancy/access (Tasks 3, 12), credentials incl. certificate-derived metadata and key/CSR lifecycle (Tasks 8, 13), template/variant/version with immutability and constraints (Tasks 3, 14, 19), Apple bundle import (Task 14), substitution incl. `${…}` and NFC payload (Tasks 4–7, 15–16), no date formatting (Task 4), projection (Tasks 10, 17), pass identity as caller UUID (Tasks 15–17), REST API (Tasks 18–20), error model and audit (Tasks 17, 19–20), retention (Task 20), operations/tests/CI (Tasks 1–2, 21, 23), Samsung deferral (enum only, Task 3).
- **Flagged deviation:** the Google `${…}` resolver lives in the builder first (Task 5) and is upstreamed in Task 22 — confirm this ordering is acceptable or move Task 22 to the front.
- **Service-body tasks (13, 14, 17):** the pure, branch-heavy units are shown as full code; the three orchestration services give exact method signatures, behaviour and pinning tests rather than a full body, because their code is straight-line glue over already-specified units. Implementers write the body against the stated signatures and the failing tests.
