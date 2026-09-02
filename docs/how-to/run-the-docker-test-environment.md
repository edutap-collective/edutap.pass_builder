# How to run the Docker test environment

This guide shows you how to build the application image, bring up its
dependencies with Docker Compose, and run the full test suite — including
the integration tests that need a real PostgreSQL database, a real object
store and a real signing certificate.

You need Docker and Docker Compose.
The commands below assume you are in the repository root.

## Build the application image

```shell
docker build -t edutap-pass-builder .
```

The `Dockerfile` is a two-stage build on `python:3.14-slim`: the first
stage installs the package with `uv`, the second copies only the installed
site-packages, the entry-point scripts, and `assets/` (which includes the
Apple WWDR intermediate certificate) into a slim, non-root final image.
If you want to verify the image builds the way CI does, run the same
command the `image` job in `.github/workflows/ci.yml` runs:

```shell
docker build -t pass-builder:ci .
```

## Bring the environment up

`compose.yml` defines three services: `db` (PostgreSQL 18), `objectstore`
(RustFS), and `app` (the service itself, built from the local
`Dockerfile`).

```shell
export EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY=$(python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")
docker compose up -d
```

Notice that `app` waits for `db`'s healthcheck before starting, so the
first request right after `up` will not race a database that is still
initializing.

Apply migrations against the running database before the service can serve
real requests.
The application image does not bundle `alembic.ini` or `migrations/`, so run
this from the host against the port `compose.yml` publishes rather than
inside the `app` container:

```shell
export EDUTAP_PASS_BUILDER_DB_HOSTS=localhost
export EDUTAP_PASS_BUILDER_DB_DATABASE=pass_builder
export EDUTAP_PASS_BUILDER_DB_USER=pass_builder
export EDUTAP_PASS_BUILDER_DB_PASSWORD=pass_builder
uv run alembic upgrade head
```

Check that the service is healthy:

```shell
curl http://localhost:8000/healthz
```

## Run the test suite

The unit and service-level test layers (`make test-local`) spin up their
own ephemeral PostgreSQL container through `testcontainers`, so they need
only a working Docker daemon — not `docker compose up` at all.

```shell
make test-local
```

The integration layer (`make test-integration`) is different: it exercises
real Apple signing and a real RustFS round trip, so it needs the compose
environment's `db` and `objectstore` services actually running and
reachable on `localhost`.

```shell
docker compose up -d db objectstore
make test-integration
```

```{note}
`test-integration` is marked `integration` in `pyproject.toml` and excluded
from `make test-local` and from CI's `test` job on purpose — it needs a
Docker daemon and real network-adjacent services that a plain unit test run
should not depend on.
```

## Tear down

```shell
docker compose down -v
```

The `-v` flag removes the named volumes, so the next `up` starts from an
empty database and an empty object store.
Omit it if you want to keep the data between runs.
