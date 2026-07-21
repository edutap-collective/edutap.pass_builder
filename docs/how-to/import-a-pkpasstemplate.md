# How to import a `.pkpasstemplate`

This guide shows you how to import an Apple `.pkpasstemplate` bundle as a
new draft version, map its fields to `data_provider` data, and publish it.
It assumes you already have a template and an Apple-typed variant; see
{doc}`/reference/rest-api` for `POST /templates` and
`POST /templates/{id}/variants` if you do not.

You need the `manage` scope on your API client.

## Build or obtain a bundle

A `.pkpasstemplate` is a flat zip archive containing `pass.json` plus the
images Apple's Wallet expects: `icon.png` (mandatory), and any of
`logo*`, `strip*`, `thumbnail*`, `background*`, `footer*`, and
`*.lproj/` localization directories.
`edutap.pass_designer` produces these bundles from its editor; you can also
assemble one by hand for testing.

If the bundle was produced by the Pass Designer, it also contains a
`tooling.json` file recording designer-only metadata.
You do not need to remove it: the import step strips it automatically.

## Import as a draft version

```shell
curl -X POST https://pass-builder.example/api/v1/variants/$VARIANT_ID/versions \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@student-id.pkpasstemplate"
```

The service decomposes the bundle completely on import:

- `pass.json` becomes the version's `pass_json` column.
- Every other file (barring `tooling.json`) becomes a separate
  `template_asset` row, addressed by its path inside the bundle — so
  localization directories such as `de.lproj/icon.png` survive intact.
- The untouched original bundle is kept as `source_object_key`, so the
  unmodified import artefact is always retrievable later.

The response has `"status": "draft"`.
If the archive has no `pass.json` entry at all, the call fails with
`422 missing_pass_json` before anything is stored.

## Inspect the imported assets

```shell
curl https://pass-builder.example/api/v1/versions/$VERSION_ID/assets/icon.png \
  -H "Authorization: Bearer $TOKEN" \
  -o icon.png
```

Static images are part of the design, not of the mapping: at render time
they are the starting state, and only an `image`-kind mapping rule ever
replaces one of them (a person's photograph, typically).
Everything else in the bundle stays exactly as designed.

If you need to swap one asset without re-importing the whole bundle — a
corrected icon, for instance — replace it directly while the version is
still a draft.

```shell
curl -X PUT https://pass-builder.example/api/v1/versions/$VERSION_ID/assets/icon.png \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@icon-fixed.png"
```

## Add mapping rules

Bind fields in `pass.json` to `data_provider` field names with a bulk
`PUT`.
Mapping rules only make sense as a complete set — the whole point of
publish-time validation is to catch every problem in that set at once — so
there is no per-rule endpoint.

```shell
curl -X PUT https://pass-builder.example/api/v1/versions/$VERSION_ID/mappings \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {"target_kind": "field_value", "target": "name", "source_field": "person.name", "value_type": "text", "required": true},
      {"target_kind": "barcode_message", "target": "message", "source_field": "person.uid", "value_type": "text", "required": true},
      {"target_kind": "image", "target": "thumbnail", "source_field": "person.photo", "value_type": "image", "required": false}
    ]
  }'
```

`target` for a `field_value` or `field_label` rule is the field's `key` as
it appears in `pass.json`, searched across every field group and pass
style block — the rule does not need to know which one.
`target` for an `image` rule is the asset filename it replaces.

This call fails with `422 invalid_mapping` if a `source_field` is not a
known `data_provider` catalogue field, or if its `value_type` does not
match — see {doc}`/reference/rest-api` for the field catalogue endpoint
that lets you check names before you use them.

## Validate before publishing

Run the full publish-time validation without committing to it.

```shell
curl -X POST https://pass-builder.example/api/v1/versions/$VERSION_ID/validate \
  -H "Authorization: Bearer $TOKEN"
```

A clean response looks like `{"valid": true, "findings": []}`.
Common findings at this stage: a mapping `target` that does not exist in
any field group, or a missing `icon.png`.

## Publish

```shell
curl -X POST https://pass-builder.example/api/v1/versions/$VERSION_ID/publish \
  -H "Authorization: Bearer $TOKEN"
```

Publishing re-runs the same validation, and on success the version becomes
immutable — its content, assets and mapping rules can no longer change.
The variant's previously published version, if any, moves to `archived`.
If you need a different mapping after this point, import a new bundle (or
reuse assets by re-uploading them) into a new draft version instead.

```{seealso}
{doc}`/explanation/why-immutable-versions` for why publishing forecloses
further edits rather than allowing them.
```
