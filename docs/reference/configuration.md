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
| `EDUTAP_PASS_BUILDER_IMAGE_SERVICE_BASE_URL` | string | `http://image_service:8000` | no |
| `EDUTAP_PASS_BUILDER_IMAGE_SERVICE_TOKEN` | secret string | `""` (unset) | no |
| `EDUTAP_PASS_BUILDER_IMAGE_SERVICE_TIMEOUT` | float, seconds | `10.0` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_ENDPOINT_URL` | string | `http://localhost:9000` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_BUCKET` | string | `pass-builder` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_ACCESS_KEY` | string | `""` | no |
| `EDUTAP_PASS_BUILDER_OBJECTSTORE_SECRET_KEY` | secret string | `""` | no |
| `EDUTAP_PASS_BUILDER_WWDR_CERTIFICATE_PATH` | path | `assets/wwdr-g4.pem` | no |
| `EDUTAP_PASS_BUILDER_AUDIT_RETENTION_MONTHS` | integer | `24` | no |
| `EDUTAP_PASS_BUILDER_UI_API_CLASS` | string | `api` | no |
| `EDUTAP_PASS_BUILDER_UI_REMOTE_USER_HEADER` | string | `REMOTE_USER` | no |
| `EDUTAP_PASS_BUILDER_UI_GROUPS_HEADER` | string | `isMemberOf` | no |
| `EDUTAP_PASS_BUILDER_UI_AUTHORISED_USERS` | string, comma separated | `""` | no |
| `EDUTAP_PASS_BUILDER_UI_AUTHORISED_GROUPS` | string, comma separated | `""` | no |

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

## `IMAGE_SERVICE_*`

Where an `IMAGE` mapping rule's reference is resolved.

A rule with `value_type = image` binds a **URL**, not a picture.
The data provider answers JSON and JSON has no bytes, so the value can only
travel as a reference.

What happens to it then depends on the platform, and the asymmetry is the
platforms' rather than ours:

- **Apple** — a `.pkpass` is a bundle and carries the picture as a file inside
  it, so the reference is fetched from the image service and the bytes go into
  the bundle.
- **Google** — an object carries images as URLs and the wallet fetches them
  itself, so the reference is substituted as it stands.
  Fetching it would put a copy of a person's photograph somewhere Google never
  reads.

`IMAGE_SERVICE_BASE_URL` is also a bound: a reference that does not start with
it is refused with `422 image_reference_rejected` and never fetched.
The reference arrives from the data provider, which makes it data rather than
configuration, and a service that fetches whatever URL it is handed reaches
hosts its caller cannot.

```{note}
Until 2026-09-01 an `IMAGE` rule could carry nothing at all, and said nothing
about it: the binder passed an image value through only when it was already
`bytes`, which a JSON value never is, so the rule bound, the version published
green and the picture was missing.
Publishing now rejects a mapping that could not place a picture — an `image`
value on a non-image target, an image target with another value type, or an
image target on a Google variant, where no asset bundle exists.
```

## `UI_*`

The management application's own zone and its allow-list — see
{doc}`/how-to/run-the-management-ui`.

`UI_API_CLASS` defaults to `api`, the zone that means Shibboleth, because the
UI has people in front of it.
The render API's `API_CLASS` stays `internal-api` and the two are separate
settings on purpose: they are separate applications in separate zones.

`UI_AUTHORISED_USERS` and `UI_AUTHORISED_GROUPS` are comma separated, and
either is enough to let someone in.

```{warning}
**Two empty lists deny everyone**, which is the same reasoning as the default
zone: an installation nobody has configured must end up unreachable rather
than standing open in front of signing credentials.
```

They are plain strings rather than lists because `pydantic-settings` parses a
list-typed field as JSON — `a,b` would have to be written `["a","b"]` in an
environment variable, a shape nobody types correctly twice.
