# Run the management UI

`edutap.pass_builder` ships two applications out of one image.
`edutap.pass_builder.app:create_app` is the render API that four services
call with a bearer token; `edutap.pass_builder.ui.app:create_ui_app` is the
management interface a person uses.

They share a database, a service layer and a master key, and they differ in
who is in front of them and which zone they sit in.

## Why two applications rather than one

`EDUTAP_PASS_BUILDER_API_CLASS` is a single setting for a whole application,
and it is what keeps `POST /passes` off a publicly reachable entry point.
Separating by router instead would make that boundary a label, and a label is
the kind of boundary that falls silently during the next rework.

## Why a portal path and not an API one

`/api/<domain>/<service>/v<n>` is the namespace for REST backends another
program calls.
This is an interface a person opens, so it lives under `/portale/<name>`,
beside the pass designer, the Kafka UI and CloudBeaver.

That is not cosmetic.
A single-page application owns a whole subtree — its bundle is fetched from
`<root>/assets/…` — and under `/api/wallet` it would squat on the prefix
`image-tools` and `admin` share, needing a web-frontend rule of its own just
for `assets`.
One portal path, one rule, covering the page, its assets and its API.

The UI mounts the management routers themselves — `templates`, `credentials`,
`fields` and `audit` — under `/tenants/{tenant_id}`, and overrides the one
dependency that establishes the caller.
`passes` is deliberately not among them.

## Configure who may use it

The UI takes its principal from the web frontend, and authorises by name or by
group.

```text
EDUTAP_PASS_BUILDER_UI_ROOT_PATH=/portale/edutap-pass-builder
EDUTAP_PASS_BUILDER_UI_AUTHORISED_USERS=alexander@example.org
EDUTAP_PASS_BUILDER_UI_AUTHORISED_GROUPS=wallet-admins
```

Either is enough, so a deployment starts with one named person and moves to a
group without a code change.

```{warning}
**Two empty lists deny everyone.**
An installation nobody has configured must end up unreachable rather than
standing open in front of signing credentials.
```

The headers are configurable because their names are a deployment's choice:
`EDUTAP_PASS_BUILDER_UI_REMOTE_USER_HEADER` (default `REMOTE_USER`) and
`EDUTAP_PASS_BUILDER_UI_GROUPS_HEADER` (default `isMemberOf`, semicolon
separated — Shibboleth's own join for a multi-valued attribute).

## Run it

```shell
uv run uvicorn edutap.pass_builder.ui.app:create_ui_app --factory --port 8001
```

In a container, the same image with a different factory:

```shell
docker run --rm -p 8001:8000 edutap-pass-builder \
  uvicorn edutap.pass_builder.ui.app:create_ui_app --factory \
  --host 0.0.0.0 --port 8000
```

Behind the web frontend nothing asserts a principal for you, so a direct
request answers `401`.
For a local look, send the headers yourself:

```shell
curl -H "REMOTE_USER: alexander@example.org" \
  http://localhost:8001/api/v1/tenants
```

## Build the interface

The single-page application is a separate build, and the Python half runs
without it — the API is complete on its own, and a developer working on
`services/` should not need a Node toolchain.

```shell
make frontend-install
make frontend-types      # regenerate the typed client from the OpenAPI document
make lint-frontend
make test-frontend
make build-frontend
```

`make frontend-types` is the one to remember: it rewrites `src/api/schema.d.ts`
from the application's own OpenAPI document, and `make lint-frontend` then
reports every call site a changed route or response model invalidated.

```{important}
**The mount point is baked into the bundle.**
Vite writes `base` into every asset URL at build time, so the value passed to
the build must match `Settings.ui_root_path` at run time.
If the two drift, the page loads, fetches its own assets from somewhere else,
and shows a white screen that no log explains.
The Docker build takes it as `EDUTAP_PASS_BUILDER_UI_ROOT_PATH`.
```

## What the interface does

* **Tenants and API tokens** — the two rows nothing else can create. Every
  render route resolves a bearer token against `api_client`, and no route
  there creates a tenant or a client, so without this the first caller could
  never be authenticated at all.
* **Templates, variants, versions** — including importing an Apple
  `.pkpasstemplate` bundle and the three files `edutap.pass_designer` exports.
* **Publishing**, with every finding at once rather than the first.
* **Credentials** — generating an Apple key and its CSR, importing an existing
  Apple pair, importing a Google service account.
* **The field catalogue**, and the `catalogue.json` the pass designer loads.

```{note}
A generated API token is displayed once.
Only its SHA-256 is stored, so a lost token is replaced rather than recovered —
a store that can show a token again is a store that can leak every token at
once.
```

## Working with the pass designer

`edutap.pass_designer` lays out a Google pass and exports `class.json`,
`object.json` and `mappings.json`.
All three go in as they stand: the first two are the body of
`POST /variants/{id}/versions`, and the third is already a
`MappingRulesRequest` — its extra `unknown_fields` key is ignored.

Point the designer at this service's catalogue rather than at its own example
file:

```text
PASS_DESIGNER_CATALOGUE_PATH=…/tenants/<tenant>/fields/catalogue.json
```

Two catalogues means a rule authored in the designer fails at publish time,
and nothing before that says why.
