# Why published versions are immutable

Once `POST /versions/{id}/publish` succeeds, a `template_version`'s
content, assets and mapping rules can no longer change.
`PUT /versions/{id}/mappings` and `PUT`/`DELETE
/versions/{id}/assets/{filename}` all answer `409 version_not_draft` once
that happens.
Changing anything means creating a new draft version instead.

## An audit reference is worthless if the reference can move

Every rendered pass writes an audit entry naming the `template_version`
that produced it.
That entry is the only record `edutap.pass_builder` keeps of the render —
the pass bytes themselves are never stored.
If a version's content could still change after passes were signed against
it, the audit trail would answer a question nobody asked ("what does
version 7 look like *today*?") instead of the one that actually matters a
year later: "what did version 7 look like when this particular pass was
issued?"
Immutability is what makes the second question answerable at all.

## Versioning at the variant, not the template

Immutability applies per variant, not across a whole template, because a
Google class is an object registered with Google with its own,
independent lifecycle.
A design fix on a "student" variant's class must not force a new version
onto an unrelated "staff" variant of the same template — the two have
nothing in common besides sharing a logical credential.
There is deliberately no version spanning an entire template; if a release
bracket across several variants ever turns out to be needed in practice, a
lightweight `template_release` — a named set of variant versions — can be
added later without disturbing this model.

## What stays mutable

Immutability is scoped narrowly, to exactly what determines rendering.
A `template`'s `name` and `description`, and a `variant`'s `name`,
`is_default` flag and `credential_set_id`, can all be updated after the
fact through `PATCH` — none of them change what a previously rendered pass
looked like, so none of them need to be frozen.
Draft versions are, of course, fully mutable until the moment they publish;
that is what the draft status is for.

```{seealso}
{doc}`/how-to/import-a-pkpasstemplate` for the practical workflow this
constraint shapes: import, map, validate, and only then publish.
```
