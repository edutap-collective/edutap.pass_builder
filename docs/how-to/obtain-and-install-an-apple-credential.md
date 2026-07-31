# How to obtain and install an Apple credential

This guide shows you how to take a tenant from having no Apple signing
capability to holding an `active` credential set, using the key generation
and certificate signing request (CSR) flow the service performs internally.

You need the `credentials` scope on your API client for every call below.

## Generate a key and a CSR

`POST /credentials` with `provider: apple` generates an RSA-2048 keypair
inside the service.
The private key never leaves it: it is sealed immediately and no endpoint
ever returns it, not even to the `credentials` scope.

```shell
curl -X POST https://pass-builder.example/internal-api/wallet/builder/v1/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "apple",
    "label": "student-id-2026",
    "common_name": "pass.example.university"
  }'
```

The response is a credential set with `"status": "key_pending"`.
Note its `id` — every following call needs it.

## Fetch the CSR and have it signed

```shell
curl https://pass-builder.example/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID/csr \
  -H "Authorization: Bearer $TOKEN" \
  -o credential.csr
```

The CSR is public, so this call works without exposing any secret.
Upload `credential.csr` to the
[Apple Developer portal](https://developer.apple.com/account/resources/identifiers/list/passTypeId),
under the Pass Type ID matching your `common_name`.
Apple returns a signed certificate; download it as `credential.pem`.

If your pass needs NFC (an Apple Wallet pass that unlocks a reader), request
the NFC-capable variant of the Pass Type ID certificate.
The service detects NFC capability from the certificate's own X.509
extension (OID `1.2.840.113635.100.6.1.26`), so nothing else changes in
this flow.

## Install the certificate

```shell
curl -X PUT https://pass-builder.example/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID/certificate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"certificate_pem\": $(python3 -c 'import json,sys; print(json.dumps(open("credential.pem").read()))')}"
```

The service verifies that the certificate's public key matches the private
key it generated, then derives every metadata field —
`pass_type_identifier`, `team_identifier`, `organization_name`,
`not_before`, `not_after`, `nfc_capable` — from the certificate itself.
None of it is ever typed in, so none of it can be mistyped.
On success the credential set's status becomes `active`.

If the certificate does not match the stored key, the call fails with
`409 certificate_key_mismatch`.
That happens if you install a certificate meant for a different credential
set — double check `$CREDENTIAL_ID`.

## Renew before expiry

Apple pass certificates expire yearly.
Poll for credentials nearing expiry and renew ahead of time.

```shell
curl "https://pass-builder.example/internal-api/wallet/builder/v1/credentials?provider=apple&expiring_within=30d" \
  -H "Authorization: Bearer $TOKEN"
```

For each one that needs renewing:

```shell
curl -X POST https://pass-builder.example/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID/renew \
  -H "Authorization: Bearer $TOKEN"
```

`renew` creates a successor credential set with a fresh keypair and CSR,
linked to the predecessor through `predecessor_id`.
Repeat the CSR and certificate steps above for the successor's `id`.
The predecessor stays `active` until it actually expires, so issuance never
stops mid-renewal.
Point your template variants at the successor's `id` (`PATCH
/variants/{id}` with `credential_set_id`) once it is active.

## Retire a credential

```shell
curl -X DELETE https://pass-builder.example/internal-api/wallet/builder/v1/credentials/$CREDENTIAL_ID \
  -H "Authorization: Bearer $TOKEN"
```

This marks the credential set `revoked`.
It is never a hard delete: the audit trail of every pass signed with it
stays intact.

## Import an existing key and certificate

When you already hold a private key and its signed certificate — the common
case when migrating an existing certificate stock — `POST /credentials`
imports the pair directly, without going through the generate-CSR flow. Send
both PEM blocks in the body; the credential set is created `active` in one
step.

```bash
curl -X POST http://localhost:8000/internal-api/wallet/builder/v1/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
        --arg key "$(cat signing_key.pem)" \
        --arg cert "$(cat signing_cert.pem)" \
        '{provider: "apple", label: "imported", private_key: $key, certificate: $cert}')"
```

The service verifies the certificate matches the private key before storing
anything; a mismatch is rejected with `409 certificate_key_mismatch`. The
imported private key never leaves the service — no response, log, or audit
entry ever contains it.

```{seealso}
{doc}`/how-to/configure-credentials-and-wwdr` for how the Apple WWDR
intermediate certificate factors into signing once a credential is active.
```
