# Why the service is stateless

`edutap.pass_builder` persists templates, variants, versions, mapping
rules and credentials.
It deliberately does not persist issued passes.
Once a `.pkpass` is signed or a Google object is pushed, the service keeps
only an audit entry: timestamp, actor, template, variant, version, wallet
type, `subject_ref`, and which fields were requested — never the pass
bytes and never a field value.

## The caller already owns the lifecycle

Whoever calls `POST /passes` already has a database row for the thing this
pass represents — a student record, a staff membership, a library card.
That row is where the pass ID, the issuance date and the current status
belong.
Duplicating that state inside `edutap.pass_builder` would create two
sources of truth for the same fact, and the two would eventually
disagree: a pass revoked in the caller's system but not mirrored here, or
a caller's row pointing at a `pass_id` this service has never heard of.

Pushing ownership outward also matches how the two wallet platforms
already work.
Apple identifies a pass by `serialNumber` and asks the *issuer's* update
web service — `edutap.apple_wallet_web_service` in this ecosystem, not
`pass_builder` — when a pass changes.
Google similarly treats an `objectId` as belonging to the issuer account
that created it.
Neither platform expects the signing service itself to be the system of
record.

## What statelessness buys

A renderer that holds no issued-pass state is trivially horizontally
scalable: any instance can handle any render request, because nothing
about a previous render needs to be found again to serve the next one.
It also has a smaller blast radius.
If `pass_builder`'s database were compromised, an attacker would find
templates, mapping rules and encrypted credential material — not a list of
every person who has ever been issued a pass and when.

## What this does not mean

Statelessness applies to *issued passes*, not to the service as a whole.
Templates, variants, versions, mapping rules and credentials are all
persisted, versioned and audited — the render path is the part that leaves
no trace of its own beyond the audit log.
`POST /passes/preview` goes one step further and writes no audit entry
at all, because no pass is produced and no person data is even requested.

```{seealso}
{doc}`/explanation/why-immutable-versions` for the audit log's other half
of the argument: an audit entry is only meaningful if what it references
cannot change.
```
