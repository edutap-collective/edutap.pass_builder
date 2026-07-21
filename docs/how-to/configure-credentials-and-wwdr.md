# How to configure credentials and the WWDR certificate

This guide shows you how to set the environment variables
`edutap.pass_builder` needs to start, and explains where the Apple WWDR
intermediate certificate fits into signing.
For the full, authoritative list of settings, see
{doc}`/reference/configuration`.

## Set the required variables

Every setting is read from the environment with the prefix
`EDUTAP_PASS_BUILDER_`, or from a `.env` file in the working directory.
Three have no default and must always be set:

```text
EDUTAP_PASS_BUILDER_DATABASE_URL=postgresql+asyncpg://user:password@host/dbname
EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY=<base64-encoded 32-byte key>
EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL=https://data-provider.example
```

Generate `SECRET_MASTER_KEY` with a cryptographically secure random source,
never by hand:

```shell
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

This key wraps every per-secret data key used to encrypt private keys and
service account files in the database.
In production it must come from Ansible Vault, injected into the
environment at deploy time — it must never enter the image or a checked-in
file.

## Configure the object store

```text
EDUTAP_PASS_BUILDER_OBJECTSTORE_ENDPOINT_URL=https://objectstore.example:9000
EDUTAP_PASS_BUILDER_OBJECTSTORE_BUCKET=pass-builder
EDUTAP_PASS_BUILDER_OBJECTSTORE_ACCESS_KEY=...
EDUTAP_PASS_BUILDER_OBJECTSTORE_SECRET_KEY=...
```

`OBJECTSTORE_ENDPOINT_URL` defaults to `http://localhost:9000` and
`OBJECTSTORE_BUCKET` to `pass-builder`, which matches the `compose.yml`
development setup but must be overridden for any real deployment.

## Point the service at `data_provider`

```text
EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL=https://data-provider.example
EDUTAP_PASS_BUILDER_DATA_PROVIDER_TOKEN=...
EDUTAP_PASS_BUILDER_DATA_PROVIDER_TIMEOUT=10.0
```

`DATA_PROVIDER_TOKEN` is optional; leave it unset if `data_provider` does
not require authentication in your deployment.
`DATA_PROVIDER_TIMEOUT` defaults to ten seconds — the render path calls
`data_provider` with this explicit timeout and retries once on a
connection failure, never on a 4xx response.

## The WWDR certificate

Apple signing needs the Apple Worldwide Developer Relations intermediate
certificate to build the trust chain from your pass-signing certificate up
to Apple's root.
Unlike the credential sets above, the WWDR certificate is not
tenant-specific and is not stored in the database: it is a single
application asset shared by every tenant, versioned by generation (`G4` at
the time of writing).

```text
EDUTAP_PASS_BUILDER_WWDR_CERTIFICATE_PATH=assets/wwdr-g4.pem
```

This is already the default, and the file ships inside the repository at
`assets/wwdr-g4.pem` — see `assets/README.md` for what it contains and how
to rotate it.
The Docker image bakes it in through `COPY assets /app/assets` in the
`Dockerfile`, so a production deployment normally needs no override at
all.
`RenderService` reads this path at signing time and passes its contents
straight to `edutap.wallet_apple`'s `sign_direct`, so a wrong or missing
path only surfaces as a signing failure on the first Apple render, not at
startup.

```{important}
If you point `WWDR_CERTIFICATE_PATH` at a custom location, the path is
resolved relative to the process's working directory, not to the
repository root — set an absolute path if the service runs from anywhere
other than the repository checkout.
```

## Retention

```text
EDUTAP_PASS_BUILDER_AUDIT_RETENTION_MONTHS=24
```

Controls how long audit log entries are kept before they become eligible
for deletion by `services.retention.purge_expired_audit`.
The default of 24 months balances traceability against the fact that
`subject_ref` plus timestamp plus requested fields accumulates into a
movement profile the longer it is retained.

```{note}
`purge_expired_audit` is a plain async function, not a scheduled job the
service runs on its own — an operator must invoke it periodically (for
example from an Ansible-managed cron job calling into the service process)
for the retention window to actually take effect.
```
