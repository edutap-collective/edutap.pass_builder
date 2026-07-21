# Data model

PostgreSQL 18, SQLModel on async SQLAlchemy, Alembic migrations.
Every primary key is a UUID.
Every timestamp is `timestamptz`.
Table definitions live in `src/edutap/pass_builder/models/db.py`.

## Tenancy and access

```text
tenant       id, key (slug, unique), name, created_at

api_client   id, tenant_id → tenant, name, token_hash,
             scopes[] ⊂ {render, manage, credentials},
             active, created_at, last_used_at
```

An API client belongs to exactly one tenant.
`tenant_id` is never accepted from a request; it is derived from the
bearer token, and every query filters on it.
There is no user management and no role hierarchy in this service — users
live in `edutap.pass_builder_manager`.

## Credentials

```text
credential_set  id, tenant_id → tenant, provider {apple|google}, label,
                status {key_pending|active|expired|revoked|superseded},
                predecessor_id → credential_set,      -- renewal chain

                -- Apple, derived from the certificate, never typed in:
                pass_type_identifier, team_identifier, organization_name,
                cert_serial, cert_fingerprint_sha256, not_before, not_after,
                nfc_capable bool, issuer_generation,

                -- Google, derived from the service account JSON:
                service_account_email, private_key_id, project_id, issuer_id,

                certificate_pem text, csr_pem text,   -- public, stored in clear
                created_at, updated_at

secret_blob     id, credential_set_id → credential_set,
                kind {private_key|service_account_json},
                ciphertext bytea, nonce bytea, wrapped_dek bytea, algo,
                created_at
```

Only genuinely secret material lives in `secret_blob`.
The certificate and the CSR are public and stay readable in
`credential_set`.

Every Apple metadata field is extracted from the certificate itself:

| Field | Source |
|---|---|
| `pass_type_identifier` | Subject `UID` |
| `team_identifier` | Subject `OU` |
| `organization_name` | Subject `O` |
| `not_before` / `not_after` | certificate validity |
| `issuer_generation` | Issuer `OU` |
| `nfc_capable` | presence of X.509 extension OID `1.2.840.113635.100.6.1.26` |

The WWDR certificate is not part of a `credential_set`.
It is a single, public, application-wide asset — see
{doc}`/reference/configuration` and `assets/README.md`.

## Template hierarchy

```text
template          id, tenant_id → tenant, key (slug), name, description,
                  created_at, archived_at
                  UNIQUE (tenant_id, key)

template_variant  id, template_id → template, wallet_type {apple|google|samsung},
                  key (slug), name, is_default bool,
                  credential_set_id → credential_set,
                  google_class_id,        -- Google only, stable class ID
                  created_at, archived_at
                  UNIQUE (template_id, wallet_type, key)
                  UNIQUE (template_id, wallet_type) WHERE is_default

template_version  id, variant_id → template_variant, number int,
                  status {draft|published|archived},
                  pass_json   jsonb,   -- Apple:  pass.json from the bundle
                  class_json  jsonb,   -- Google: class definition (design)
                  object_json jsonb,   -- Google: object template with ${…}
                  source_object_key,   -- untouched original bundle in RustFS
                  nfc_enabled bool,
                  nfc_encryption_public_key text,
                  nfc_requires_authentication bool,
                  notes, created_at, created_by → api_client, published_at
                  UNIQUE (variant_id, number)
                  UNIQUE (variant_id) WHERE status = 'published'
                  CHECK pass_json IS NOT NULL
                        OR (class_json IS NOT NULL AND object_json IS NOT NULL)

template_asset    id, version_id → template_version, filename, media_type,
                  size, sha256, object_key, created_at
                  UNIQUE (version_id, filename)
```

Three levels:

- **Template** — the logical credential, for example "student ID".
- **Variant** — one design for one wallet platform.
  Google delegates design to the class for everything except
  `GenericObject`, so a group with its own design needs its own class, and
  therefore its own variant.
- **Version** — everything that determines rendering: content, assets,
  mapping rules, placeholder inventory.

A published version is immutable, including its assets and mapping rules.
Changes create a new version.
See {doc}`/explanation/why-immutable-versions` for why.

Versioning sits at variant level, not template level: a Google class has
its own lifecycle at Google, and a design fix on one variant's class must
not force a new version on every other variant of the same template.

## Substitution

```text
mapping_rule  id, version_id → template_version,
              origin {authored|derived},
              target_kind, target text,
              source_field text,        -- data_provider field, e.g. person.name
              value_type {text|date|number|boolean|image|uri},
              required bool, default_value text, position int
```

One table serves both platforms, distinguished by `origin`:

- **`authored`** — Apple rules, maintained through the mappings endpoint.
  `target` is a field `key` or an image slot.
  The `.pkpasstemplate` itself is never modified.
- **`derived`** — Google rules.
  On publish the service scans `object_json` for `${…}` occurrences,
  computes a JSON pointer for each, and writes the rows itself; they are
  not editable through the API.

`target_kind` values:

| Value | Meaning |
|---|---|
| `field_value` | `target` is a field key |
| `field_label` | `target` is a field key, binds the label instead of the value |
| `barcode_message` | |
| `barcode_alt_text` | |
| `image` | `target` is an asset filename, replaces that asset |
| `nfc_payload` | Apple `pass.nfc.message`, Google `smartTapRedemptionValue` |
| `json_pointer` | escape hatch, RFC 6901, never creates missing intermediate nodes |

The serial number is not a mapping target: it always comes from the
request (see {doc}`/explanation/why-placeholders` for pass identity).

NFC configuration lives on the version, not in the mapping — the
encryption key comes from the reader, is identical for every pass of a
version, and has no per-person component:

| Field | Apple | Google |
|---|---|---|
| `nfc_enabled` | emits `pass.nfc` | sets `enableSmartTap` on the class |
| `nfc_encryption_public_key` | `pass.nfc.encryptionPublicKey` | unused |
| `nfc_requires_authentication` | `pass.nfc.requiresAuthentication` | unused |

## Field catalogue and audit

```text
data_field  id, key, value_type, label, required, description, fetched_at
            UNIQUE (key)

audit_log   id, tenant_id, ts, request_id, actor_client_id,
            action, outcome {success|error}, error_code, duration_ms,
            template_id, variant_id, version_id, wallet_type,
            subject_ref,              -- the person_uid
            requested_fields text[],
            details jsonb             -- never person data, never secrets
```

`data_field` caches the field catalogue published by `data_provider`.
Every `mapping_rule.source_field` is validated against it on save, so a
wrong field name or a type conflict fails at authoring time rather than at
the five-hundredth render.

`audit_log` records one entry per call with an effect, including failures.
Actions in use: `pass.create`, `pass.update`, `pass.save_link`,
`template.publish`, `variant.sync`, `credential.create`,
`credential.certificate_installed`, `credential.renew`,
`credential.revoke`.
`preview` writes no audit entry — no pass is produced and no person data
flows.
Retention defaults to 24 months; see
{doc}`/reference/configuration`.
