# edutap.pass_builder — Design

Date: 2026-07-21
Status: approved (design phase)

## 1. Purpose and scope

`edutap.pass_builder` is an internal, **stateless** FastAPI service that turns a
stored pass template plus person data into a wallet pass:

- Apple: a signed `.pkpass` file
- Google: a wallet object pushed to Google, plus a decoupled save link
- Samsung: reserved, not implemented

It persists **templates, variants, versions, mapping rules and credentials**.
It does **not** persist issued passes. The caller owns and stores whatever it
hands out. The only record the service keeps of a render is an audit entry.

### Out of scope

| Concern | Owner |
|---|---|
| Persisting issued passes, pass IDs | the calling service |
| Device registration, APNs push, pass update web service | `edutap.apple_wallet_web_service` |
| Filling templates, user management, UI | `edutap.pass_builder_manager` |
| Graphical pass design | `edutap.pass_designer` |
| Person data | `edutap.data_provider` |
| Samsung wallet | deferred — `edutap.wallet_samsung` has no code |

### Preconditions in other packages

Two upstream work packages must land before or alongside this service.

| Precondition | Status on 2026-07-21 |
|---|---|
| `edutap.wallet_apple`: `api.from_template(file)` loading `.pkpasstemplate` | PR [#39](https://github.com/edutap-eu/edutap.wallet_apple/pull/39) open, +75/−0, tests green — must be merged and released |
| `edutap.wallet_google`: `${…}` placeholder resolution against a mapping | not started — separate work package |

The Google placeholder resolver must stay as minimal as PR #39: one function
taking a model or dict plus a mapping, replacing `${…}` occurrences in **string
values only**. No filters, no expression language, no code execution.

## 2. Architecture

Four layers with a single rule: the engine is pure, everything impure sits
outside it.

| Layer | Responsibility | Depends on |
|---|---|---|
| API | FastAPI routers, API-client authentication, request validation | Service |
| Service | template lifecycle, credential lifecycle, render orchestration | Repository, Engine, Clients |
| Engine | **pure**: (version content + assets + resolved data) → pass | `wallet_apple`, `wallet_google` only |
| Adapters | `data_provider` client, `SecretBackend`, object store | — |

The engine performs all substitution and is fully testable without a database
and without network access: literals in, bytes out.

### Render flow

```
POST /passes  { pass_id, template, variant?, wallet_type, person_uid }
  → resolve version (published, or pinned)
  → derive required fields from the version's mapping rules
  → data_provider: request ONLY those fields for person_uid      [projection]
  → decrypt credentials through SecretBackend
  → engine: bind, convert, apply, build                          [pure]
  → write audit entry
  → .pkpass bytes  |  { object_id, class_id, … }
```

Data minimisation is structural: the service never asks for a whole person
record, only for the fields the published version provably needs. The audit
entry records exactly which fields were requested.

## 3. Data model

PostgreSQL 18, SQLModel on async SQLAlchemy, Alembic migrations. All primary
keys are UUIDs, all timestamps `timestamptz`.

### Tenancy and access

```
tenant       id, key (slug, unique), name, created_at

api_client   id, tenant_id →tenant, name, token_hash,
             scopes[] ⊂ {render, manage, credentials},
             active, created_at, last_used_at
```

An API client belongs to exactly one tenant. `tenant_id` is never accepted from
a request; it is derived from the token, and every query filters on it.

The service has no user management and no role hierarchy. Users live in the
manager.

### Credentials

```
credential_set  id, tenant_id →tenant, provider {apple|google}, label,
                status {key_pending|active|expired|revoked|superseded},
                predecessor_id →credential_set,      -- renewal chain

                -- Apple, derived from the certificate, never typed in:
                pass_type_identifier, team_identifier, organization_name,
                cert_serial, cert_fingerprint_sha256, not_before, not_after,
                nfc_capable bool, issuer_generation,

                -- Google, derived from the service account JSON:
                service_account_email, private_key_id, project_id, issuer_id,

                certificate_pem text, csr_pem text,   -- public, stored in clear
                created_at, updated_at

secret_blob     id, credential_set_id →credential_set,
                kind {private_key|service_account_json},
                ciphertext bytea, nonce bytea, wrapped_dek bytea, algo,
                created_at
```

Only genuinely secret material lives in `secret_blob`. The certificate and the
CSR are public and stay readable.

**All Apple metadata is extracted from the certificate**, verified against real
LMU certificates:

| Field | Source | Example |
|---|---|---|
| `pass_type_identifier` | Subject `UID` | `pass.demo.lmu.de` |
| `team_identifier` | Subject `OU` | `JG943677ZY` |
| `organization_name` | Subject `O` | `Ludwig-Maximilians-Universitaet Muenchen` |
| `not_before` / `not_after` | validity | `2024-10-14` → `2025-11-13` |
| `issuer_generation` | Issuer `OU` | `G4` |
| `nfc_capable` | X.509 extension OID `1.2.840.113635.100.6.1.26` | present ⇒ true |

The NFC extension is the reliable signal; the common name (`Pass Type ID with
NFC:` versus `Pass Type ID:`) carries the same information as free text and is
not used for the decision.

The WWDR certificate is **not** part of a credential set. It is public, ships
with the deployment as an application asset, and is versioned by generation.

### Secret storage

The schema knows only a `SecretBackend` protocol. One implementation ships:
**encrypted in the database** (AES-GCM, one data key per secret, master key from
the environment, injected via Ansible Vault). The protocol costs little and
keeps crypto calls out of the rest of the code base, so a future external store
becomes a new class rather than a migration.

### Credential lifecycle (Apple)

The private key is generated inside the service and never leaves it.

1. `POST /credentials` with subject data → RSA-2048 keypair generated
   internally, status `key_pending`. The key is encrypted immediately and is not
   retrievable through any endpoint.
2. `GET /credentials/{id}/csr` → CSR in PEM, uploaded by the operator to the
   Apple Developer portal.
3. `PUT /credentials/{id}/certificate` → the signed certificate. The service
   verifies that the certificate's public key matches the stored private key and
   that the chain validates against WWDR G4, then derives all metadata and sets
   status `active`.
4. `POST /credentials/{id}/renew` → successor with a fresh keypair and CSR,
   linked to its predecessor. The old set stays `active` until it expires, so
   renewal never interrupts issuance.

Importing an existing key together with its certificate remains supported for
the existing certificate stock.

### Template hierarchy

```
template          id, tenant_id →tenant, key (slug), name, description,
                  created_at, archived_at
                  UNIQUE (tenant_id, key)

template_variant  id, template_id →template, wallet_type {apple|google|samsung},
                  key (slug), name, is_default bool,
                  credential_set_id →credential_set,
                  google_class_id,        -- Google only, stable class ID
                  created_at, archived_at
                  UNIQUE (template_id, wallet_type, key)
                  UNIQUE (template_id, wallet_type) WHERE is_default

template_version  id, variant_id →template_variant, number int,
                  status {draft|published|archived},
                  pass_json   jsonb,   -- Apple:  pass.json from the bundle
                  class_json  jsonb,   -- Google: class definition (design)
                  object_json jsonb,   -- Google: object template with ${…}
                  source_object_key,   -- untouched original bundle in RustFS
                  nfc_enabled bool,
                  nfc_encryption_public_key text,
                  nfc_requires_authentication bool,
                  notes, created_at, created_by →api_client, published_at
                  UNIQUE (variant_id, number)
                  UNIQUE (variant_id) WHERE status = 'published'
                  CHECK apple  ⇒ pass_json IS NOT NULL
                  CHECK google ⇒ class_json IS NOT NULL AND object_json IS NOT NULL

template_asset    id, version_id →template_version, filename, media_type,
                  size, sha256, object_key, created_at
                  UNIQUE (version_id, filename)
```

Three levels, each earning its place:

- **Template** — the logical credential ("student ID"), the shared bracket.
- **Variant** — one per wallet type **and design**. Google delegates design to
  the class for everything except `GenericObject`, so a group with its own
  design is its own class and therefore its own variant. Apple carries the same
  structure through a separate template bundle.
- **Version** — everything that determines rendering: content, assets, mapping
  rules, placeholder inventory.

A published version is **immutable**, including its assets and mapping rules.
Changes create a new version. Immutability is what makes the audit log
meaningful a year later.

Versioning sits at variant level, not template level: a Google class is an
object registered with Google with its own lifecycle, and a design fix on the
student class must not re-version the staff class. There is deliberately no
version spanning the whole credential; if a release bracket turns out to be
needed, a lightweight `template_release` (a named set of variant versions) can
be added later.

### Apple template import

A `.pkpasstemplate` is a flat archive. On import it is decomposed completely:

```
POST …/versions  (multipart: bundle.pkpasstemplate)
  → pass.json                              → template_version.pass_json
  → icon*, logo*, strip*, thumbnail*,
    background*, footer*, *.lproj/*        → template_asset (one row per file)
  → the untouched original bundle          → template_version.source_object_key
```

`template_asset.filename` holds the path relative to the bundle root, so
localisation directories survive if present. `wallet_apple` takes the files as
they are into `PkPass.files`, and the manifest covers all of them.

Static images are part of the design, not of the mapping. At render time the
assets are the starting state; only `image` mapping rules replace individual
files (a photograph, for instance). Everything else stays as designed.

The Pass Designer writes a designer-only `tooling.json`. `api.from_template()`
strips it on load, so the service does not handle it — but the retained original
bundle still contains it, which is correct: it is the unmodified import
artefact.

Assets live in RustFS, content-addressed as `<tenant>/<version_id>/<sha256>`.
Because versions are immutable, an object is never overwritten, and identical
images across versions occupy space once.

### Substitution

```
mapping_rule  id, version_id →template_version,
              origin {authored|derived},
              target_kind, target text,
              source_field text,        -- data_provider field, e.g. person.name
              value_type {text|date|number|boolean|image|uri},
              required bool, default_value text, position int
```

One table for both platforms, distinguished by `origin`:

- **Apple → `authored`.** Rules are maintained through the API; `target` is the
  field `key` or an image slot. The `.pkpasstemplate` is never modified. No
  code, no restricted Python, no parser over user-supplied content.
- **Google → `derived`.** On publish the service scans `object_json` for `${…}`,
  computes a JSON pointer per occurrence and writes the rows itself. Not
  editable.

The payoff: one query yields the projection field list for any version,
regardless of platform —
`SELECT DISTINCT source_field FROM mapping_rule WHERE version_id = …`.

`target_kind` values:

| Value | Meaning |
|---|---|
| `field_value` | `target` = field key |
| `field_label` | |
| `barcode_message` | |
| `barcode_alt_text` | |
| `image` | `target` = image slot, replaces an asset |
| `nfc_payload` | Apple `pass.nfc.message` · Google `smartTapRedemptionValue` |
| `json_pointer` | escape hatch, RFC 6901, does not create missing intermediate nodes |

A version always belongs to a variant, and a variant always has a wallet type,
so a rule can never be ambiguous. There is therefore **one** `nfc_payload`
target rather than platform-specific names; the platform difference is handled
as validation, not as schema:

- Apple: at most 64 characters. Since the value comes from data, length is
  checked at render time with a clear error, never by signing an unreadable
  pass.
- Google: up to 32 kB, effectively unbounded.

The serial number is **not** a mapping target. It comes from the request (see
pass identity), and two sources for one identity would only surface at the first
update.

The NFC configuration fields sit on the version, not in the mapping: the
encryption key comes from the reader, not from the person, and is identical for
all passes of a version.

| Field | Apple | Google |
|---|---|---|
| `nfc_enabled` | `pass.nfc` is emitted | `enableSmartTap` on the class |
| `nfc_encryption_public_key` | `pass.nfc.encryptionPublicKey` | unused — the key belongs to the issuer, i.e. the credential set |
| `nfc_requires_authentication` | `pass.nfc.requiresAuthentication` | unused |

For Google versions the last two stay empty; setting them is rejected on save.

### Field catalogue and audit

```
data_field  id, key, value_type, label, required, description, fetched_at
            UNIQUE (key)

audit_log   id, tenant_id, ts, request_id, actor_client_id,
            action, outcome {success|error}, error_code, duration_ms,
            template_id, variant_id, version_id, wallet_type,
            subject_ref,              -- the person_uid
            requested_fields text[],
            details jsonb             -- never person data, never secrets
```

`data_field` caches the catalogue published by `data_provider`. Every
`mapping_rule.source_field` is validated against it **on save**, so a wrong field
name or a type conflict fails immediately rather than on the five-hundredth
pass. The manager uses the same catalogue to offer valid rules instead of only
rejecting invalid ones. The catalogue also decouples release cycles: a shared
Python model would force coordinated releases across four services.

## 4. Rendering engine

```python
def render(version: RenderSpec, data: Mapping[str, Any], creds: Credentials) -> RenderResult
```

`RenderSpec` is a Pydantic model populated from the database but unaware of it,
so every test case is a literal.

### Four steps, in order

1. **Bind.** For each mapping rule, take `data[source_field]`. A missing
   `required` field without a default aborts with `missing_field` naming **all**
   missing fields at once.
2. **Convert.** By `value_type`: `date` → ISO 8601 with time zone, `number` →
   canonical decimal, `text` → unchanged. **No formatting, no localisation.**
3. **Apply.** By `target_kind`. Apple: fields located by their `key` across all
   field groups; `image` replaces an entry in the file collection. Google: the
   `${…}` pass over `object_json`, replacing **string values only**, never keys,
   with `$$` as the escape for a literal `$`.
4. **Build.**

   Apple:
   ```
   api.from_template(bundle)   → PkPass with pass.json and all static files
     → apply mapping           (fields, images, nfc_payload, barcode)
     → api.sign(settings=…)
     → api.pkpass()            → .pkpass bytes
   ```
   Load first, then substitute. Not `api.new()` with a pre-modified dict — that
   would bypass the bundle.

   Google:
   ```
   apply mapping to object_json      (${…} resolution)
     → api.new("…Object", data=…)
     → api.create(…) | api.update(…) → push to Google
   ```
   The save link is **decoupled** into its own endpoint. It is a signed JWT with
   `iat`/`exp`, so it is time-bound and regenerable, and `save_link()` warns
   beyond 1800 bytes of JWT — both argue against tying it to creation time.

### No date formatting

Both platforms format dates themselves, declaratively: Apple through
`dateStyle`, `timeStyle`, `ignoresTimeZone` and `isRelative` on an ISO 8601
value; Google through its `DateTime`/`TimeInterval` structures. The device
renders in the user's language.

The service therefore emits ISO 8601 and never formats. Any formatting in the
builder would freeze one language into a multilingual product. A date destined
for a plain text field must be delivered pre-formatted by `data_provider`;
mapping a `date` source onto a non-date target is rejected.

### Validation happens twice

- **On publish** — once, thorough: does every `target` exist in the template?
  Does the catalogue know every `source_field`, and do the types match? Is the
  certificate valid, and NFC-capable if `nfc_enabled`? Does the template
  validate against the `wallet_*` Pydantic models? Is every mandatory asset
  present (`icon.png`)?
- **On render** — per call, cheap: only what depends on data. Missing values,
  `nfc_payload` length, barcode payload.

Consequently a render can no longer fail because of a template defect. Template
errors surface to the manager, not to the end user.

### Determinism

Same version plus same data yields the same pass, with two documented
exceptions: the cryptographic signature (timestamped) and any
`authenticationToken`. Determinism is what makes the update endpoint
predictable.

### Pass identity

**The pass ID is a UUID supplied by the caller and persisted by the caller.**
The service neither generates nor stores it.

| Platform | Derivation |
|---|---|
| Apple | `serialNumber` = the UUID |
| Google | `objectId` = `{issuer_id}.{uuid}` |

The ID contains no template, no variant and no `person_uid`. Variant switching
happens solely through the `classId` on an otherwise unchanged object, which is
only possible if the object ID is independent of the variant.

### Variant selection

The caller passes `variant` explicitly. Without it the variant flagged
`is_default` for that template and wallet type is used. The service evaluates no
rules and infers nothing.

## 5. REST API

Prefix `/api/v1`. Bearer token of an `api_client`. **`tenant_id` never appears
in a path or body** — it comes from the token alone. Errors as
`application/problem+json` (RFC 9457). Lists are cursor-paginated. Every call
carries an `X-Request-Id` through to the audit log.

### Rendering — scope `render`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/passes` | create a pass |
| `PUT` | `/passes/{pass_id}` | update a pass — **variant may change here** |
| `POST` | `/passes/{pass_id}/save-link` | Google: generate a save link |
| `POST` | `/passes/preview` | dry run: substitute, do not sign or push |

```http
POST /api/v1/passes
{ "pass_id": "f2c1…-uuid", "template": "studierendenausweis",
  "wallet_type": "apple", "variant": "student",
  "person_uid": "…", "template_version": 7 }
```

`variant` and `template_version` are optional and default to the default variant
and the published version.

Responses differ by platform, deliberately:

- **Apple** → `200`, `application/vnd.apple.pkpass`, bytes. Metadata
  (`template_version`, `variant`, `credential_set`) as `X-` headers, so the
  caller can record them without parsing the body.
- **Google** → `201`, JSON `{ pass_id, object_id, class_id, template_version,
  variant }`. The push has happened; the save link is not included.

`PUT /passes/{pass_id}` takes the same body without `pass_id`. For Apple that
means new bytes with an identical serial number; for Google an update of the
same object, possibly with a different `classId`.

`POST /passes/preview` returns the resolved `pass.json` or object JSON plus the
list of bound fields, without signing and without contacting Google. It takes
`{ template, wallet_type, variant?, template_version?, sample_data? }`. Values
are taken from `sample_data`; any field it does not cover is filled with a
generated placeholder derived from the rule's `value_type` (for example
`«person.name»` for text, the current date for `date`). `data_provider` is never
called, so no person data flows and the endpoint works on `draft` versions too.

### Templates — scope `manage`

| Method | Path | Purpose |
|---|---|---|
| `GET` `POST` | `/templates` | list, create |
| `GET` `PATCH` `DELETE` | `/templates/{id}` | read, change, archive |
| `GET` `POST` | `/templates/{id}/variants` | variants |
| `GET` `PATCH` | `/variants/{id}` | including `is_default`, `credential_set_id` |
| `POST` | `/variants/{id}/sync` | **push the Google class** — idempotent, any time |
| `GET` `POST` | `/variants/{id}/versions` | `POST` creates a `draft` |
| `GET` | `/versions/{id}` | |
| `GET` `PUT` | `/versions/{id}/mappings` | mapping rules, **bulk replace**, draft only |
| `GET` `PUT` `DELETE` | `/versions/{id}/assets/{filename}` | draft only |
| `POST` | `/versions/{id}/validate` | full validation without publishing |
| `POST` | `/versions/{id}/publish` | validate → `published`, predecessor → `archived` |

Creating a version is platform-specific: Apple via `multipart/form-data` with
the `.pkpasstemplate`, Google via JSON with `class_json` and `object_json`. Both
land as `draft`; `publish` makes them immutable.

Publishing a Google version is what triggers the class push; `/variants/{id}/sync`
exists so the manager can re-push the current state at any time. The manager
decides when, the builder executes, because the builder holds the credentials
and the template content. No state is added on this side: the state lives at
Google.

Mapping rules are a bulk `PUT` rather than per-row CRUD, because a rule set is
only meaningful — and only validatable — as a whole.

### Credentials — scope `credentials`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/credentials?provider=&expiring_within=90d` | metadata only |
| `POST` | `/credentials` | generate a key, or import existing material |
| `GET` | `/credentials/{id}/csr` | fetch the CSR (PEM) |
| `PUT` | `/credentials/{id}/certificate` | install the signing certificate → `active` |
| `POST` | `/credentials/{id}/renew` | successor with a fresh keypair |
| `DELETE` | `/credentials/{id}` | `revoked`, never a hard delete |

**No endpoint ever returns secret material**, not even masked, not even to the
`credentials` scope. The private key travels in one direction only.

`expiring_within` exists because Apple pass certificates expire yearly and an
expired certificate silently stops issuance. At the time of writing all four
inspected LMU certificates were expired, the most recent since 2026-05-02.

### Catalogue, audit, operations

| Method | Path | Scope |
|---|---|---|
| `GET` | `/fields` | `manage` — cached `data_provider` catalogue |
| `POST` | `/fields/refresh` | `manage` |
| `GET` | `/audit?from=&to=&template=&subject_ref=&outcome=` | `manage` |
| `GET` | `/healthz` `/readyz` | open |

`/readyz` checks the database, RustFS and `data_provider` reachability, so the
orchestrator does not put a container into service that cannot build a pass.

## 6. Errors and audit

Every error is `application/problem+json` with a stable machine-readable `type`
slug. Clients react to the slug, never to the text.

| HTTP | Slug | Trigger |
|---|---|---|
| 400 | `invalid_request` | schema violation |
| 401 / 403 | `unauthenticated` / `insufficient_scope` | missing token / wrong scope |
| 404 | `template_not_found`, `variant_not_found`, `version_not_found` | also for another tenant's objects — **never 403**, which would reveal existence |
| 409 | `version_not_draft`, `already_published`, `default_variant_conflict` | lifecycle violation |
| 409 | `credential_expired`, `credential_not_nfc_capable`, `certificate_key_mismatch` | credential state |
| 422 | `missing_field` | required field not delivered — lists **all** missing fields |
| 422 | `nfc_payload_too_long` | Apple, beyond 64 characters |
| 422 | `template_validation_failed` | on publish, listing every finding |
| 502 | `data_provider_unavailable`, `wallet_provider_error` | upstream failure |
| 504 | `upstream_timeout` | |

Two principles matter more than the list:

1. **Validation errors are collected, not thrown one at a time.** Whoever
   publishes a template or builds a pass wants every problem at once.
2. **No person data and no secret in an error message or a log line.**
   `missing_field` names field *names*, never values. The `wallet_*` libraries
   never receive a debug dump of resolved data.

### Upstream behaviour

- `data_provider`: explicit timeout, one retry on connection failure, **no**
  retry on 4xx. A single `httpx.AsyncClient` reused across the `lifespan`.
- Google: no blind retry on `create`. A 409 "already exists" is the normal
  outcome of a repeated call, since the object ID comes from the caller, and is
  treated as success.
- No circuit breaker in version 1. Two services, internal traffic — complexity
  without benefit.

### Audit

An entry is written for every call with an effect, **including failures** — a
failed render is the more interesting row.

Actions: `pass.create`, `pass.update`, `pass.save_link`, `template.publish`,
`variant.sync`, `credential.create`, `credential.certificate_installed`,
`credential.renew`, `credential.revoke`.

Recorded: timestamp, request ID, client, outcome, duration, `subject_ref` (the
`person_uid`), template, variant, version, wallet type, `requested_fields`, and
the error slug on failure. Not recorded: field values, pass bytes, any part of a
secret.

The write happens in the **same transaction** as the change that triggered it,
so `template.publish` is atomic. Rendering has no transaction; there the entry is
written on completion, and if the insert fails the request fails. A missing pass
is preferable to an untraceable one.

Retention is configurable, defaulting to 24 months, enforced by a background
sweep. The `person_uid` is a non-speaking identifier, but the combination of
identifier, timestamp and requested fields accumulates into a movement profile
over years.

`preview` writes no audit entry: no pass is produced and no person data flows.

## 7. Implementation, tests, operations

### Stack

Python 3.12+, async throughout. FastAPI, Pydantic v2, pydantic-settings,
SQLModel on async SQLAlchemy, PostgreSQL 18, Alembic, httpx, `wallet_apple`,
`wallet_google`, `cryptography` (key generation, CSR, certificate parsing,
AES-GCM), an S3-compatible client for RustFS.

```
src/edutap/pass_builder/
    settings.py      pydantic-settings, prefix EDUTAP_PASS_BUILDER_
    models/          SQLModel tables (db.py) and API schemas (api.py), separate
    engine/          pure substitution and build: binding, apply, apple, google
    services/        template, credential and render orchestration
    clients/         data_provider (httpx), objectstore (RustFS)
    secrets/         SecretBackend protocol, database-encrypted implementation
    routers/         passes, templates, credentials, fields, audit, health
    app.py           FastAPI application, lifespan
```

Database models and API schemas are separate types. Otherwise every schema
change leaks outward, and `secret_blob` must never be serialisable by accident.

### Tests

Test-first, in three layers.

| Layer | Coverage | Network / database |
|---|---|---|
| Engine | substitution, type conversion, every `target_kind`, `$$` escaping, missing fields, NFC length, determinism | none |
| Service / API | lifecycles (draft → published, credential chain), scopes, tenant isolation, error slugs | PostgreSQL container; `data_provider` and Google through `respx` |
| Integration (`make test-integration`) | real signing against a test certificate, RustFS from compose, verifying the `.pkpass` with `api.verify()` | compose |

Three mandatory cases, because they catch the expensive failures:

1. **Tenant isolation** — client A reaching for client B's template yields `404`,
   parameterised across every endpoint.
2. **No secret leaves the service** — every response of every endpoint is
   checked against the known key material.
3. **Immutability** — any modification of a `published` version yields `409`,
   for mappings, assets and content alike.

Google calls in unit tests always go through `respx`. No real Google traffic
outside `test-integration`.

### Operations

Multi-stage Dockerfile on `python:3.14-slim`, non-root. `compose.yml` with the
application, PostgreSQL 18 and RustFS for local runs and CI. Deployment through
the existing Docker Swarm and Ansible path; the **master key for secret
encryption** reaches the environment from Ansible Vault and never enters the
image.

Makefile targets: `lint`, `reformat`, `test-local`, `test-integration`. CI as
GitHub Actions mirroring the local run plus an image build. Documentation under
`docs/` following Diataxis with Sphinx and MyST.

## 8. Decisions and rationale

| Decision | Rationale |
|---|---|
| Stateless, issued passes not persisted | the caller owns the lifecycle; the audit log covers traceability |
| Input is `template_id` + `person_uid` + wallet type | the service resolves data itself through `data_provider` |
| Template → variant → version | Google delegates design to the class, so one credential needs several classes |
| Explicit variant with a fixed default | no rule engine, no second evaluator to debug |
| Apple: mapping rules, template untouched | no user-supplied code, no restricted Python, no parser over foreign templates |
| Google: `${…}` in the JSON | readable in place, valid strings, no invasive change to `wallet_google` models |
| `${…}` rather than `$ref` | `$ref` is taken by JSON Schema and JSON Reference |
| No formatting in the builder | both platforms format declaratively and localise on device |
| Immutable published versions | an audit reference is worthless if the referenced version can change |
| Credential metadata derived from the certificate | no field can be mistyped |
| Key generated in the service, CSR issued by it | the private key never travels |
| Multi-tenancy from the start | retrofitting it means touching every table, endpoint and query |
| Pass ID as a caller-supplied UUID | variant switching requires an ID independent of the variant |

## 9. Deliberately deferred

- Samsung wallet — an enum value and a `501` stub while the library is empty.
- `template_release` as a bracket over several variant versions — only if a real
  need appears.
- Formatting options on mapping rules (`format`, `locale`) — only if a date in a
  plain text field turns out to be unavoidable.
- An external secret store — the protocol is in place; a new class suffices.
- Circuit breakers, rate limiting.
