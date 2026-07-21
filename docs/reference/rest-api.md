# REST API

Every endpoint lives under the prefix `/api/v1`, except `/healthz` and
`/readyz`.
Authentication is a bearer token identifying one `api_client`, which
belongs to exactly one tenant.

```text
Authorization: Bearer <token>
```

`tenant_id` never appears in a path or a request body.
It is derived from the token alone, and every query is filtered on it.
A request for another tenant's object returns `404`, never `403` — a `403`
would confirm the object exists.

Every endpoint below requires the scope named in its section heading,
except `/healthz` and `/readyz`, which require no authentication at all.

## Rendering — scope `render`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/passes` | create a pass |
| `PUT` | `/passes/{pass_id}` | update a pass — the variant may change here |
| `POST` | `/passes/{pass_id}/save-link` | Google: generate a save link |
| `POST` | `/passes/preview` | dry run: substitute, never sign or push |

### `POST /passes`

Request body (`CreatePassRequest`):

```json
{
  "pass_id": "f2c1e9a0-1234-4a5b-8c6d-abcdef123456",
  "template": "student-id",
  "wallet_type": "apple",
  "variant": "default",
  "person_uid": "ada",
  "template_version": 7
}
```

`variant` and `template_version` are optional.
Omitting `variant` selects the variant flagged `is_default` for the given
template and `wallet_type`.
Omitting `template_version` selects the published version.

The response differs by platform:

- **Apple** — `200`, body `application/vnd.apple.pkpass`, the signed
  `.pkpass` bytes.
  Metadata travels as response headers: `X-Template-Version`, `X-Variant`,
  and `X-Credential-Set` when a credential set was used.
- **Google** — `201`, JSON:

  ```json
  {
    "pass_id": "f2c1e9a0-1234-4a5b-8c6d-abcdef123456",
    "object_id": "3388000000022...ada",
    "class_id": "3388000000022...student-id",
    "template_version": 7,
    "variant": "default"
  }
  ```

  The push to Google has already happened by the time this response
  returns; the save link is not included (see below).

### `PUT /passes/{pass_id}`

Same body as `POST /passes`, minus `pass_id` (`UpdatePassRequest`).
Re-renders and re-delivers: for Apple, new signed bytes under the same
serial number; for Google, an update of the same object, possibly onto a
different `classId` if `variant` changed.

### `POST /passes/{pass_id}/save-link`

Request body (`SaveLinkRequest`): `template`, optional `variant`, optional
`template_version`.
Returns `{"save_link": "<jwt>"}` — a time-bound, regenerable JWT for
Google's "save to wallet" flow, deliberately decoupled from creation time.

### `POST /passes/preview`

Request body (`PreviewRequest`): `template`, `wallet_type`, optional
`variant`, optional `template_version`, optional `sample_data`.
Any mapped field not covered by `sample_data` is filled with a generated
placeholder for its `value_type` (`"Sample Text"`, `"2024-01-01"`, `"1"`,
`"true"`, a sample URI, or a 1x1 PNG for `image`).
`data_provider` is never called, so this works on `draft` versions and
writes no audit entry.

Response (`PreviewResponse`):

```json
{
  "pass_json": { "...": "..." },
  "object_json": null,
  "bound_fields": ["person.name"]
}
```

## Templates — scope `manage`

| Method | Path | Purpose |
|---|---|---|
| `GET`, `POST` | `/templates` | list, create |
| `GET`, `PATCH`, `DELETE` | `/templates/{id}` | read, update, archive |
| `GET`, `POST` | `/templates/{id}/variants` | list, create variants |
| `GET`, `PATCH` | `/variants/{id}` | read, update — including `is_default`, `credential_set_id` |
| `POST` | `/variants/{id}/sync` | push the Google class — idempotent, any time |
| `GET`, `POST` | `/variants/{id}/versions` | list versions, create a `draft` |
| `GET` | `/versions/{id}` | read one version |
| `GET`, `PUT` | `/versions/{id}/mappings` | read / bulk-replace mapping rules — `PUT` only while `draft` |
| `GET`, `PUT`, `DELETE` | `/versions/{id}/assets/{filename}` | read / replace / remove an asset — `PUT`/`DELETE` only while `draft` |
| `POST` | `/versions/{id}/validate` | run publish validation without publishing |
| `POST` | `/versions/{id}/publish` | validate, then `published`; archives the predecessor |

`DELETE /templates/{id}` archives — it is never a hard delete — and returns
`200` with the archived template, not `204`.

### `POST /variants/{id}/versions`

The content type decides the import path:

- `multipart/form-data` with a `file` field carrying a `.pkpasstemplate`
  archive — see {doc}`/how-to/import-a-pkpasstemplate`.
- `application/json` with `class_json` and `object_json` — a Google
  version.

Both land as `draft`.

### `POST /variants/{id}/sync`

Re-pushes a Google variant's currently published class definition to
Google.
Rejects a non-Google variant with `400 not_a_google_variant` before ever
touching credential material, and a Google variant with no credential set
configured with `409 google_credentials_missing`.

## Credentials — scope `credentials`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/credentials?provider=&expiring_within=90d` | list, metadata only |
| `POST` | `/credentials` | generate an Apple key/CSR, or import a Google service account |
| `GET` | `/credentials/{id}/csr` | fetch the CSR, PEM |
| `PUT` | `/credentials/{id}/certificate` | install the signed certificate → `active` |
| `POST` | `/credentials/{id}/renew` | successor with a fresh keypair |
| `DELETE` | `/credentials/{id}` | mark `revoked` — never a hard delete, `204` |

No endpoint here ever returns secret material — not the private key, not
the service account JSON — to any scope.
`POST /credentials` needs `common_name` for `provider: apple`, or
`issuer_id` and `service_account_json` for `provider: google`; either
missing is `400 invalid_request`.

```{note}
Importing an existing Apple key and certificate pair (as opposed to
generating a fresh key through this endpoint) is implemented at the
service layer only — see
{doc}`/how-to/obtain-and-install-an-apple-credential` — and is not yet
reachable through `POST /credentials`.
```

Full lifecycle walkthrough: {doc}`/how-to/obtain-and-install-an-apple-credential`.

## Catalogue, audit, operations

| Method | Path | Scope |
|---|---|---|
| `GET` | `/fields` | `manage` — the cached `data_provider` catalogue |
| `POST` | `/fields/refresh` | `manage` — replace the cache from `data_provider` |
| `GET` | `/audit?from_=&to=&template=&subject_ref=&outcome=` | `manage` |
| `GET` | `/healthz` | open |
| `GET` | `/readyz` | open |

`/readyz` independently checks the database, the object store and
`data_provider`, and returns `503 not_ready` with a `checks` object naming
which dependency failed if any of the three is unreachable.

```{important}
The audit list's date-range query parameter is `from_`, not `from` —
`from` is a reserved word in Python and the endpoint does not declare an
alias for it.
```

## Errors

Every error is `application/problem+json` (RFC 9457).
`type` is `urn:edutap:pass-builder:<slug>`; clients should match on the
slug, never on `title` or `detail`.

| HTTP | Slug | Trigger |
|---|---|---|
| 400 | `invalid_request` | schema or payload violation |
| 400 | `not_a_google_variant` | `/variants/{id}/sync` on a non-Google variant |
| 400 | `unsupported_wallet_type` | internal guard, should not surface in practice |
| 401 | `unauthenticated` | missing or unknown bearer token |
| 403 | `insufficient_scope` | token lacks the required scope |
| 404 | `template_not_found`, `variant_not_found`, `version_not_found`, `asset_not_found`, `credential_not_found` | including another tenant's object — never `403` |
| 409 | `version_not_draft` | mapping/asset change or publish on a non-draft version |
| 409 | `certificate_key_mismatch` | installed certificate does not match the stored key |
| 409 | `google_credentials_missing`, `google_class_not_configured`, `apple_credentials_missing` | render or sync without the needed credential configuration |
| 422 | `missing_field` | a required field was not returned by `data_provider`; lists every missing field at once |
| 422 | `invalid_mapping` | a mapping rule fails catalogue validation on save |
| 422 | `missing_pass_json` | an imported bundle has no `pass.json` entry |
| 422 | `template_validation_failed` | publish-time validation found problems; lists every finding |
| 422 | `invalid_service_account` | a Google service account JSON fails to parse |
| 500 | `internal_error` | any unexpected exception; the original message is never surfaced or audited |
| 502 | `data_provider_unavailable` | `data_provider` unreachable after one retry |
| 503 | `not_ready` | `/readyz`, at least one dependency check failed |

```{note}
An Apple NFC payload over 64 characters currently surfaces as `500
internal_error` rather than a dedicated `422` slug: the engine raises a
plain exception for it, which the render path's catch-all maps to
`internal_error` like any other unexpected error. Everything else in this
table matches the design spec's intent.
```
