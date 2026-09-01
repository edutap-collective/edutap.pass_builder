# Design: A management UI inside the pass builder

**Date:** 2026-09-01
**Status:** agreed, implementation under way

## The decision in one sentence

The templates, mapping rules and signing credentials this service persists are
managed from a user interface that lives in this repository, as a second ASGI
application beside the render API, rather than from a separate
`edutap.pass_builder_manager` service.

## Why not a separate service

`edutap.pass_builder_manager` existed as a README and a LICENCE, describing a
management layer *on top of the REST API*. Two findings retired that shape.

**The client cannot express management.** `edutap.pass_builder_api` covers
`create_pass`, `create_apple_pass`, `create_google_pass`, `update_pass`,
`save_link`, `deactivate_pass`, `preview`, `healthz` and `readyz` — the consumer
surface, nothing else. No template, no variant, no version, no mapping rule, no
credential, no tenant. The `manage` and `credentials` scopes exist in this service
and in its REST routes, but not in the client.

**Nothing can create the first caller.** Every route resolves a bearer token
against `api_client` in `auth.py`, and no endpoint creates a tenant or an API
client. A manager speaking REST could administer everything except the credential
it needs to speak at all. The tutorial says as much: create them "by hand … where
SQL is unavoidable".

A user interface inside this service has no such problem. It authenticates a
person, not a machine, and it creates the tenant and the API clients as its first
act.

## Shape

Two ASGI applications, one image, one database, one Alembic history.

| | `app.py` | `ui.py` |
|---|---|---|
| Callers | four services rendering passes | people |
| Auth | bearer token → `api_client` → scopes | `REMOTE_USER` from Shibboleth → LDAP group |
| Zone | `internal-api` — no web frontend entry | `api` — behind the web frontend |
| Surface | render, plus the REST management routes | person-scoped routes for its own client |

**Two applications rather than two routers on one.** `api_class` is a single
setting for a whole application, and the zone is what keeps `POST /passes`
off a publicly reachable entry point. Splitting by router would make that
boundary a label rather than a zone, and a label is the kind of boundary that
falls silently during the next rework.

**The UI calls the service layer directly.** `TemplateService`,
`CredentialService`, `validate_mapping_rules` and `write_audit` — the same
objects the routers use, with their own session. Going out to HTTP and back
would buy nothing and would need a token this application does not have.

This is why the service layer stays the place where the guarantees live, and why
none of them may migrate into the routers: a published version stays immutable,
mapping rules are validated against the field catalogue, secret material stays
wrapped under the master key, and every management action leaves an audit row —
for the UI exactly as for a REST caller.

## Frontend

React on the stack `edutap.pass_designer` already runs: Vite, TanStack Query,
`openapi-typescript` against this service's own OpenAPI document, i18next,
vitest, pnpm.

The designer lays out a Google pass and exports `class_json`, `object_json` with
`${dotted.field}` placeholders and `mappings.json` "in the shape
`edutap.pass_builder` already defines". Today those three files travel by
download and upload. They are meant to stop travelling: the designer is to grow
into this UI. Sharing its stack makes that a move of components; two stacks would
make the detour permanent.

The cost is accepted and named: a Node build stage in a Dockerfile that is
otherwise plain Python, and a person-scoped HTTP surface beside the token-scoped
one, because a React client needs an API and the existing one authorises per
`api_client`.

## Scope of the first cut

Create a tenant · create a template, a variant, a version · import an Apple
`.pkpasstemplate` bundle · import the designer's three files · edit mapping rules
· publish · upload Apple and Google credentials · create `api_client` tokens ·
export the field catalogue for the designer.

Deliberately out:

* **The credential lifecycle** — generating a key, producing a CSR, taking a
  signed certificate back. Existing certificates are uploaded. The service can do
  all of it (`CredentialService.create_apple`, `get_csr`, `install_certificate`);
  it just has no mask yet, and the first deployment has certificates already.
* **The designer itself.** It stays its own service for now.

## Images on a pass

An `IMAGE` mapping rule carries a URL, and the service fetches the bytes from
`edutap.image_service`.

It has to change, because today the rule cannot carry anything at all:
`DataProviderClient.fetch_fields` returns `response.json()`, `engine/binding.py`
passes an `IMAGE` value through only `if isinstance(value, bytes)`, and JSON has
no bytes. The condition can never hold. The `return str(value)` below it takes
over, and `engine/apple_apply.py` writes the asset only for bytes — so the rule
binds, validates green, publishes green, and the picture is missing.

`_validate_for_publish` therefore rejects an `IMAGE` rule the service cannot
resolve. A silent failure here is worse than a loud one: nothing observable
separates a pass that was designed without a picture from a pass whose picture
was swallowed.

## Deployment settings

The service moves onto what its siblings already run:
`edutap.db_definitions.ClusterSettings` for the multi-host DSN, primary selection
and TLS; `secrets_dir=/run/secrets` so the master key, the database password and
the object store keys arrive as files rather than environment values; and
`edutap.observability_settings`.

The lower bound on `edutap.db_definitions` is `>=0.3.2`. Earlier versions declare
no `secrets_dir`, and a rebuild would otherwise resolve one of them — a service
that reads no secret is indistinguishable from a deployment that has none.

```{warning}
Rotating `EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY` makes every stored credential
unusable; the AES key wrapping derives from it. Two applications now hold it,
which changes nothing about that — but it doubles the places a rotation has to
reach.
```
