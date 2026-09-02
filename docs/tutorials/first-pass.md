# Build your first pass

In this tutorial we will take a freshly checked out `edutap.pass_builder`,
bring up its dependencies, and issue a signed Apple Wallet pass end to end.
Along the way you will create a tenant and an API client by hand (the
service ships no admin UI of its own), import an Apple credential, import a
`.pkpasstemplate` bundle, publish it, and render a pass from it.

We will use `curl` throughout so every step is visible on the wire, and a
short Python snippet only where SQL is unavoidable.

## Before you start

You need:

- Docker and Docker Compose, to run PostgreSQL and RustFS.
- `uv`, to install and run the service.
- A signed Apple pass type certificate and its private key.
  If you do not have one yet, follow
  {doc}`/how-to/obtain-and-install-an-apple-credential` first, then come
  back here.

## Step 1: install and configure the service

Clone the repository and install its development extras.

```shell
git clone https://github.com/edutap-eu/edutap.pass_builder
cd edutap.pass_builder
make install
```

Bring up PostgreSQL and RustFS in the background.

```shell
docker compose up -d db objectstore
```

Create a `.env` file with the settings the service needs.
`SECRET_MASTER_KEY` wraps every secret the service stores, so generate a
fresh one rather than typing something memorable.

```shell
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

```text
EDUTAP_PASS_BUILDER_DB_HOSTS=localhost
EDUTAP_PASS_BUILDER_DB_DATABASE=pass_builder
EDUTAP_PASS_BUILDER_DB_USER=pass_builder
EDUTAP_PASS_BUILDER_DB_PASSWORD=pass_builder
EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY=<paste the key you just generated>
EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL=http://localhost:9999
EDUTAP_PASS_BUILDER_OBJECTSTORE_ENDPOINT_URL=http://localhost:9000
EDUTAP_PASS_BUILDER_OBJECTSTORE_ACCESS_KEY=pass_builder
EDUTAP_PASS_BUILDER_OBJECTSTORE_SECRET_KEY=pass_builder
```

`data_provider` is not part of this tutorial, so the URL above is a
placeholder; the person data we render with will come from
`sample_data` in the preview call at the end, which never contacts it.

Apply the database migrations and start the service.

```shell
uv run alembic upgrade head
uv run uvicorn edutap.pass_builder.app:create_app --factory --reload
```

Notice that the process logs a `Uvicorn running on http://127.0.0.1:8000`
line.
Leave it running and open a second terminal for the rest of this tutorial.

## Step 2: create a tenant and an API client

Tenant and API client provisioning has no REST endpoint of its own: a
tenant is the unit of billing and isolation, and creating one is an
operational act, not a self-service one.
Insert the rows directly and print a bearer token you will reuse for every
request below.

```shell
uv run python3 - <<'PY'
import asyncio
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from edutap.pass_builder.auth import hash_token
from edutap.pass_builder.models.db import ApiClient, Tenant
from edutap.pass_builder.models.enums import Scope
from edutap.pass_builder.settings import get_database_settings


async def main() -> None:
    engine = create_async_engine(get_database_settings().async_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    async with session_factory() as session:
        tenant = Tenant(key="tutorial", name="Tutorial Tenant")
        session.add(tenant)
        await session.flush()
        session.add(
            ApiClient(
                tenant_id=tenant.id,
                name="tutorial-client",
                token_hash=hash_token(token),
                scopes=[Scope.RENDER, Scope.MANAGE, Scope.CREDENTIALS],
                active=True,
            )
        )
        await session.commit()
    print(f"export TOKEN={token}")


asyncio.run(main())
PY
```

Copy the printed `export TOKEN=...` line into your shell.
Every request from here on carries `Authorization: Bearer $TOKEN`.

## Step 3: import your Apple credential

If you completed
{doc}`/how-to/obtain-and-install-an-apple-credential`, you have a signed
certificate on disk.
Import the matching key and certificate as an existing credential set.

```shell
CREDENTIAL_ID=$(curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider": "apple", "label": "tutorial", "common_name": "pass.tutorial.example"}' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
```

You should see a `key_pending` credential set with metadata derived
straight from the certificate you install in the next step: no field here
was typed in by hand.

Fetch the CSR, have it signed through the Apple Developer portal (the
how-to guide covers this), then install the resulting certificate.

```shell
curl -s http://localhost:8000/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID/csr \
  -H "Authorization: Bearer $TOKEN" > tutorial.csr

curl -s -X PUT http://localhost:8000/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID/certificate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"certificate_pem\": $(python3 -c 'import json,sys; print(json.dumps(open("signed.pem").read()))')}"
```

The response now shows `"status": "active"`, plus `pass_type_identifier`,
`team_identifier` and `organization_name` extracted straight from the
certificate.

## Step 4: create a template and a variant

A template is the logical credential — "student ID" — and a variant is one
design for one wallet platform.

```shell
TEMPLATE_ID=$(curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/templates \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key": "student-id", "name": "Student ID"}' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')

VARIANT_ID=$(curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/templates/$TEMPLATE_ID/variants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"key\": \"default\", \"name\": \"Default\", \"wallet_type\": \"apple\", \"is_default\": true, \"credential_set_id\": \"$CREDENTIAL_ID\"}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
```

## Step 5: import a `.pkpasstemplate` bundle

Follow {doc}`/how-to/import-a-pkpasstemplate` to build a
`.pkpasstemplate` bundle, or use one produced by
`edutap.pass_designer`.
Import it as a draft version of the variant you just created.

```shell
VERSION_ID=$(curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/variants/$VARIANT_ID/versions \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@student-id.pkpasstemplate" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
```

The response has `"status": "draft"`.
Notice that the service decomposed the bundle: `pass.json` became the
version's content, and every image inside became a separate asset row you
can inspect through `GET /builder/v1/versions/$VERSION_ID/assets/icon.png`.

## Step 6: add a mapping rule and publish

A draft version needs at least one mapping rule that binds a field in the
pass to a `data_provider` field name, so the render step has something to
substitute.
Replace `name` below with the `key` of a field already present in your
`pass.json`.

```shell
curl -s -X PUT http://localhost:8000/internal-api/wallet/builder/v1/versions/$VERSION_ID/mappings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"rules": [{"target_kind": "field_value", "target": "name", "source_field": "person.name", "value_type": "text", "required": true}]}'
```

Publish the version.
Publishing runs full validation once — every `target` must exist in the
template, every `source_field` must be a known catalogue field, the
certificate must be valid — and, once it succeeds, the version becomes
immutable.

```shell
curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/versions/$VERSION_ID/publish \
  -H "Authorization: Bearer $TOKEN"
```

You should see `"status": "published"` in the response.
Any further attempt to change this version's mappings or assets now fails
with `409 version_not_draft`.

## Step 7: render your first pass

Before touching `data_provider`, preview the render.
`POST /passes/preview` never signs, never pushes, and never contacts
`data_provider`: it substitutes `sample_data` and hands back the resolved
`pass.json`.

```shell
curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/passes/preview \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"template": "student-id", "wallet_type": "apple", "sample_data": {"person.name": "Ada Lovelace"}}'
```

You should see `"bound_fields": ["person.name"]` and a `pass_json` with
`"value": "Ada Lovelace"` in the field you mapped.
Once the preview looks right, create the real pass, this time supplying a
`person_uid` and a UUID you generate and keep — the service never invents
or stores this ID, your caller does.

```shell
curl -s -X POST http://localhost:8000/internal-api/wallet/builder/v1/passes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pass_id": "f2c1e9a0-1234-4a5b-8c6d-abcdef123456", "template": "student-id", "wallet_type": "apple", "person_uid": "ada"}' \
  -o ada.pkpass
```

`ada.pkpass` is a signed, ready-to-open Apple Wallet pass.
Open it on a Mac or AirDrop it to an iPhone to see it land in Wallet.

## What you built

You provisioned a tenant, imported a signing credential whose metadata came
entirely from the certificate itself, imported and published an
immutable template version, and rendered a signed pass without ever
touching Apple's signing tools directly.
Every step you ran is now in the audit log, retrievable through
`GET /builder/v1/audit`.

From here, {doc}`/reference/rest-api` documents every endpoint you used
and the ones you did not, and {doc}`/explanation/why-immutable-versions`
explains why step 6 could not be undone.
