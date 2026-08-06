# CLAUDE.md — edutap.pass_builder

Repository-specific rules. They take precedence over the global defaults.

## Language

**English only.** This repository belongs to eduTAP proper, not to any single
institution: README, changelog, documentation, docstrings, code comments, commit
messages, pull request titles and bodies, and replies to review comments.

The language follows the repository, not the conversation. A discussion held in
German still produces English artefacts here.

## What this service is

Manages pass templates, their variants and versions, the mapping rules that bind
fields to places in a pass, and the signing credentials. It renders passes; it does
not decide who gets one.

## Guard rails

**A published version is immutable.** Templates version rather than change: correcting
a published version means publishing a new one. Anything that edits a published row
breaks the guarantee every rendered pass relies on.

**`Template.key` is a cross-package contract.** `pass_state.pass_template` in
`edutap.data_provider` carries it as a plain string, deliberately without a foreign
key. Renaming a key silently orphans state rows in another service's database.

**Rotating the secret master key makes every stored credential unusable.** The AES
key-wrapping around the signing credentials is derived from it. Rotation is a
migration, not a configuration change.

**`data_field` is a cache, not a source.** The field catalogue belongs to
`edutap.data_provider`; the copy here exists to validate mapping rules at publish
time. Never treat it as authoritative and never write to it from anywhere else.

**The migration history is shared.** Every eduTAP package lives in one database and
one schema today, and this package uses Alembic's default version table. Until that is
resolved, adding a migration means checking that no other package claims the same
table.

## Sources and confidentiality

**No vendor internals — from any vendor, not just the ones currently in play.**
Neither in files nor in commit messages.

The standard is academic: a statement counts as reliable only where it can be
evidenced from public information, with a link. Everything else came from a protected
source, from our own testing, or from insider knowledge, and the four are not
interchangeable:

* **Documented** — public source, linked. May be written as fact.
* **Verified, not citable** — obtained by a person from an access-protected area and
  checked there; the reference is recorded internally but must not be published; and
  the statement has been reduced to what is not confidential. May be written as fact,
  carrying this label. It is the rule journalism uses for source protection: the claim
  stands, we know where it comes from, the reader does not get the source.

  The four conditions hold together. A statement for which nobody can name the
  internal reference does not fall here — that is insider knowledge.
* **Measured** — established by our own tests. May be written down, but always marked
  as such, because it describes what a platform did on the day we looked, not what it
  guarantees. It can change with the next release, without notice and without an entry
  in any changelog.
* **Insider knowledge** — is not written down at all.

What a platform's behaviour *means for us* stays documentable even where the mechanism
does not.

Contract and regulatory material is wanted and citable: eduPersonAssurance, GÉANT and
eduGAIN terms, published wallet programme obligations.

## Working practice

Branch first, never commit on `main`. Push only when asked. Lint and tests green
before opening a pull request.

Design records under `docs/superpowers/` record a decision at a point in time — do not
rewrite them to match a later state; write a new one.
