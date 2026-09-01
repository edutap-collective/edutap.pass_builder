# Configuration

`edutap.pass_builder` reads its configuration through `pydantic-settings`,
from environment variables prefixed `EDUTAP_PASS_BUILDER_` or from a
`.env` file in the process's working directory.
The settings model is `edutap.pass_builder.settings.Settings`.

| Variable | Type | Default | Required |
|---|---|---|---|
| `EDUTAP_PASS_BUILDER_DB_HOSTS` | string | — | yes |
| `EDUTAP_PASS_BUILDER_DB_DATABASE` | string | — | yes |
| `EDUTAP_PASS_BUILDER_DB_USER` | string | — | yes |
| `EDUTAP_PASS_BUILDER_DB_PASSWORD` | secret string | — | yes |
| `EDUTAP_PASS_BUILDER_DB_SSLMODE` | string | driver default | no |
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

## `DB_*`

Where the cluster is, spelled as fields rather than as one DSN.
`DatabaseSettings` derives from
`edutap.db_definitions.settings.ClusterSettings`, which builds the async URL
from them.

`DB_HOSTS` names **every** node, comma separated, each optionally with its own
port:

```text
EDUTAP_PASS_BUILDER_DB_HOSTS=pg-a,pg-b:5433,pg-c
```

Naming a single node is what breaks at the next failover.
`target_session_attrs=read-write` selects the primary among them; without it a
connection lands on a replica, *succeeds*, and only the first write fails — so
the mistake surfaces far from its cause.

There is deliberately no `DATABASE_URL`.
It existed until 2026-09-01 and carried the password inside the string, which
kept it in the process environment — and therefore in `docker inspect` and in
the frame locals an error tracker collects.

## Secrets as files

`Settings` and `DatabaseSettings` both declare `secrets_dir=/run/secrets`, so
the master key, the database password and the object-store key can arrive as
mounted files instead of environment values.

`pydantic-settings` has **no `_FILE` convention**.
It reads a secret file only where `secrets_dir` points, and the file name
carries the prefix:

```text
/run/secrets/EDUTAP_PASS_BUILDER_secret_master_key
/run/secrets/EDUTAP_PASS_BUILDER_DB_password
```

A secret mounted under the bare field name is silently ignored.
A missing directory is harmless — `pydantic-settings` warns and falls back to
the environment, so a development machine without `/run/secrets` is unaffected.

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
