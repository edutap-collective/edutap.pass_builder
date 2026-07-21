# Why substitution uses placeholders, not code

`edutap.pass_builder` substitutes person data into a template through two
narrow, declarative mechanisms — mapping rules for Apple, `${…}`
placeholders for Google — and deliberately not through anything that
looks like a templating language.

## No code, no parser, over content someone else wrote

A `.pkpasstemplate` bundle and a Google class/object definition both
arrive from outside the service: from `edutap.pass_designer`, from an
operator, eventually from any tenant with the `manage` scope.
Anything that evaluates expressions against untrusted input — a
Jinja-style templating language, a restricted-Python `eval`, even a small
custom expression grammar — is an attack surface, and a debugging burden
that grows with every function someone decides the mini-language should
support.

Apple's mapping rules sidestep this entirely: a rule names a `target`
(a field key or an image slot) and a `source_field`, and the engine copies
one value into one place.
There is no expression to evaluate, so there is nothing to sandbox.

## `${…}` is readable in place, not a language

Google's route is different because the object is JSON the caller
provides, not a fixed schema of named fields the service defines.
Rather than replace the whole document with an opaque template ID, the
service scans `object_json` for `${…}` occurrences directly inside string
values, and resolves each one against the caller's mapping — readable in
the JSON itself, valid at every intermediate stage, and requiring no
invasive change to the `wallet_google` models it eventually becomes.

The resolver `wallet_google` exposes for this is intentionally minimal:
one function, a model or dict plus a mapping, string values only, `$$` as
the escape for a literal `$`.
No filters, no expression language, no code execution — matching Apple's
mapping rules in spirit even though the mechanism looks different, because
the object shape differs between the two platforms.

`${…}` rather than the more familiar `$ref` because `$ref` already means
something else twice over: JSON Schema uses it for schema composition,
and JSON Reference (RFC 6901's sibling) uses it for document links.
Reusing that syntax for value substitution would have made every existing
`$ref` in a class or object definition ambiguous.

## No formatting, ever

The same instinct rules out formatting.
Apple renders dates through `dateStyle`, `timeStyle`, `ignoresTimeZone`
and `isRelative` on an ISO 8601 value; Google renders them through its own
`DateTime`/`TimeInterval` structures.
Both platforms already localise on the device, in the user's language.
If the builder formatted a date into a string before handing it to either
platform, it would freeze one language and one style into a product used
across every language Apple and Google Wallet support.
So the service emits ISO 8601 and nothing else: mapping a `date` source
onto a plain text target is rejected outright, and a date destined for
free text must arrive pre-formatted from `data_provider`, where it is a
deliberate choice rather than an accident of the substitution engine.

```{seealso}
{doc}`/reference/data-model` for `mapping_rule`'s exact columns and
`target_kind` values.
```
