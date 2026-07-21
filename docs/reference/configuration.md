# Configuration

`edutap.pass_builder` reads its configuration through `pydantic-settings`,
from environment variables prefixed `EDUTAP_PASS_BUILDER_` or from a
`.env` file in the process's working directory.
The settings model is `edutap.pass_builder.settings.Settings`.

| Variable | Type | Default | Required |
|---|---|---|---|
| `EDUTAP_PASS_BUILDER_DATABASE_URL` | string | — | yes |
| `EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY` | secret string | — | yes |
| `EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL` | string | — | yes |
| `EDUTAP_PASS_BUILDER_DATA_PROVIDER_TOKEN` | secret string | `""` (unset) | no |
| `EDUTAP_PASS_BUILDER_DATA_PROVIDER_TIMEOUT` | float, seconds | `10.0` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_ENDPOINT_URL` | string | `http://localhost:9000` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_BUCKET` | string | `pass-builder` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_ACCESS_KEY` | string | `""` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_SECRET_KEY` | secret string | `""` | no |
| `EDUTAP_PASS_BUILDER_WWDR_CERTIFICATE_PATH` | path | `assets/wwdr-g4.pem` | no |
| `EDUTAP_PASS_BUILDER_AUDIT_RETENTION_MONTHS` | integer | `24` | no |

## `DATABASE_URL`

An async SQLAlchemy connection string.
The service is built against `asyncpg`, so it must use the
`postgresql+asyncpg://` scheme:

```text
postgresql+asyncpg://user:password@host:5432/dbname
```

## `SECRET_MASTER_KEY`

A base64-encoded 32-byte AES key.
It wraps the per-secret data key used to encrypt every private key and
service account file stored in `secret_blob` (`DatabaseSecretBackend`,
AES-GCM).
In production this key comes from Ansible Vault, injected into the
environment at deploy time; it must never be committed or baked into the
image.
An incorrect length raises `ValueError: master key must be 32 bytes
(base64 encoded)` when the secret backend is constructed.

## `DATA_PROVIDER_BASE_URL` / `DATA_PROVIDER_TOKEN` / `DATA_PROVIDER_TIMEOUT`

Configure the `httpx.AsyncClient` used to fetch person data and the field
catalogue from `data_provider`.
`DATA_PROVIDER_TOKEN` is optional and empty by default; set it only if
your `data_provider` deployment requires bearer authentication.
`DATA_PROVIDER_TIMEOUT` is the explicit per-request timeout in seconds; the
render path retries once on a connection failure and never on a 4xx
response.

## `OBJECTSTORE_*`

Configure the S3-compatible client used to store template assets and the
untouched original `.pkpasstemplate` bundle, content-addressed as
`<tenant>/<version_id>/<sha256>`.
The defaults match the `compose.yml` development setup (RustFS) and must
be overridden for any real deployment.

## `WWDR_CERTIFICATE_PATH`

Filesystem path to the Apple WWDR intermediate certificate (PEM), used to
build the signing chain for every Apple pass.
Defaults to `assets/wwdr-g4.pem`, which ships in the repository and is
copied into the Docker image by `COPY assets /app/assets`.
See {doc}`/how-to/configure-credentials-and-wwdr` and `assets/README.md`
for what the file contains and how to rotate it.
Relative paths resolve against the process's working directory, not the
repository root.

## `AUDIT_RETENTION_MONTHS`

The number of months an `audit_log` entry is kept before
`services.retention.purge_expired_audit` considers it eligible for
deletion.
This function is not scheduled by the service itself; an operator must
invoke it periodically for entries to actually be purged.
