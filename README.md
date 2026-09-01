# edutap.pass_builder

A stateless FastAPI service that turns a stored pass template plus person
data into a wallet pass: a signed `.pkpass` for Apple Wallet, or a pushed
object plus a save link for Google Wallet.
Samsung is reserved but not implemented.

It ships as **two applications out of one image**: the render API four
services call with a bearer token, and a management interface a person uses.
They share a database, a service layer and a master key; see
[Run the management UI](docs/how-to/run-the-management-ui.md).

The service persists templates, variants, versions, mapping rules and
signing credentials.
It does not persist issued passes — the calling service owns and stores
whatever it hands out — and the only record it keeps of a render is an
audit entry.

Full documentation, including a tutorial, how-to guides, the REST API
reference and the data model, lives under `docs/`; see
[Documentation](#documentation) below.

## Installation

The package is not published to PyPI yet. Install it straight from the
source repository:

```console
uv pip install git+https://github.com/edutap-eu/edutap.pass_builder
```

It depends on `edutap.wallet-apple` and `edutap.wallet-google`, published
to PyPI in their own right, for the actual Apple and Google Wallet
protocols — `pass_builder` only orchestrates templates, credentials and
substitution around them.

## Quickstart

Clone the repository and install it in editable mode with the development
extras.

```console
git clone https://github.com/edutap-eu/edutap.pass_builder
cd edutap.pass_builder
make install
make lint
make test-local
```

`make test-local` runs the unit and service-level test suite; it spins up
its own ephemeral PostgreSQL container through `testcontainers` and needs
only a working Docker daemon, not `docker compose up`.

## The management interface

A React application in front of the same service layer, with its own targets
so the Python half stays runnable without a Node toolchain:

```console
make frontend-install
make frontend-types      # regenerate the typed client from the OpenAPI document
make lint-frontend
make test-frontend
make build-frontend
```

Run it beside the render API:

```console
uv run uvicorn edutap.pass_builder.ui.app:create_ui_app --factory --port 8001
```

Who may use it is an allow-list of principals or groups, taken from the
headers the web frontend asserts. **Two empty lists deny everyone** — an
installation nobody has configured must end up unreachable rather than
standing open in front of signing credentials.

Configuration is read from the environment, prefixed with
`EDUTAP_PASS_BUILDER_` — see `src/edutap/pass_builder/settings.py` or
`docs/reference/configuration.md` for the full list.

## Docker test environment

`compose.yml` brings up PostgreSQL, RustFS and the application together,
which the integration test suite (`make test-integration`) and the
end-to-end docs tutorial both need.

```console
docker build -t edutap-pass-builder .
docker compose up -d db objectstore
make test-integration
docker compose down -v
```

See `docs/how-to/run-the-docker-test-environment.md` for the full
walkthrough, including running migrations and checking `/healthz`.

## Documentation

Build the Sphinx + MyST documentation locally:

```console
uv pip install -e ".[docs]"
uv run sphinx-build -b html docs docs/_build/html
```

It follows the [Diataxis](https://diataxis.fr/) framework:

- **Tutorial** — `docs/tutorials/first-pass.md`, building and rendering a
  pass end to end.
- **How-to guides** — `docs/how-to/`, covering the Apple credential
  lifecycle, importing a `.pkpasstemplate`, the Docker test environment,
  and configuring credentials and the WWDR certificate.
- **Reference** — `docs/reference/`, the REST API, the data model and the
  configuration settings.
- **Explanation** — `docs/explanation/`, the design rationale behind
  statelessness, the substitution mechanism, and immutable versions.
