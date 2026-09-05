# 0001 — Administration moves out of this service

**Date:** 2026-09-05
**Status:** accepted
**Supersedes:** `docs/superpowers/specs/2026-09-01-management-ui-design.md`

## Context

This service ships two ASGI applications from one image: `app.py`, which renders
passes for four machine callers, and `ui/app.py`, which lets a person manage
templates, credentials, API clients and the field catalogue. The second went to
production on 2026-09-05 and is what the LMU deployment is configured from today.

That shape was decided on 2026-09-01, against a separate
`edutap.pass_builder_manager` speaking REST. Two findings retired the separate
service then, and both were correct:

**The client could not express management.** `edutap.pass_builder_api` covered
the consumer surface — `create_pass`, `update_pass`, `deactivate_pass`,
`preview`, health — and no template, variant, version, mapping rule, credential
or tenant.

**Nothing could create the first caller.** Every route resolved a bearer token
against `api_client`, and no endpoint created a tenant or an API client. A
manager speaking REST could have administered everything except the credential
it needed in order to speak at all. The tutorial said as much: create them by
hand, where SQL is unavoidable.

The in-process interface had neither problem. It authenticates a person rather
than a machine, and creating the tenant and its API clients was its first act.

**What changed between then and now** is not that those findings were wrong. It
is that the question grew, and two of their premises were removed on purpose:

1. **The question is no longer about one service.** The estate runs
   `edutap.data_provider`, `edutap.image_service`, the Apple VAS signing service
   and this one, and every one of them needs administering. The signing service
   already carries its own server-rendered interface in htmx. An interface that
   lives inside this service can never administer any of the others — so the
   choice is not "one UI here or one UI there", it is "one interface per
   service, in whatever stack each was written in, forever".

2. **The bootstrap problem was configuration, not architecture.** As of
   2026-09-05 a tenant is declared in a service's settings and reconciled into
   its database at startup; it is deployment topology, not runtime data. And an
   administrator is established by the deployment's identity provider — HTTP
   Basic with a form locally, SAML or OIDC at a site — and mapped to permissions
   through settings. Neither the first tenant nor the first administrator has to
   be created through an API any more. The egg moved into configuration, and the
   chicken-and-egg went with it.

## Decision

**Administration leaves this service.** A separate product,
`edutap.admin_ui`, administers every eduTAP service through each service's own
**admin API**.

This service therefore gains a third mount of the management routers —
`templates`, `credentials`, `fields`, `audit` — under an admin prefix, with a
configurable person-authentication in front of it and a permission check on
every route. The routers themselves are unchanged: they never knew who was
calling, and `current_auth` still decides.

**The consumer API stays on the internal entry point. The admin API is published
on the back-office entry point as a second router.** The zone argument from the
superseded spec holds unchanged, and this ADR is careful to keep it: `POST
/passes` must not become reachable from a browser, and the boundary that
prevents it stays a zone rather than a label.

`ui/app.py` stays until `edutap.admin_ui` replaces it screen by screen, and is
then removed. It is what the 2026-09-15 workshop is configured from; it does not
get to wobble first.

`edutap.pass_builder_manager` stays scrapped. `edutap.admin_ui` is not that
service under a new name: the manager was a management layer over *this*
service's consumer API, and this is an interface over *every* service's admin
API.

## Rationale

**One interface per service is a cost that compounds.** Four services means four
stacks, four auth models, four sets of empty states, and four places to
implement the next cross-cutting requirement. The estate already has two —
React here, htmx in the signing service — and they share nothing.

**The alternative was a shared component library with the interfaces staying
in-process.** It was proposed and rejected. It keeps each interface simple and
gives a common look, but it leaves an administrator moving between four
addresses with four sessions to see the state of one pass, and it does not
answer the question that started this: where does the *next* interface go.

**A permission is `<object>:<verb>@<tenant>`.** The tenant belongs inside the
permission because the same person holds different rights in different tenants:
whoever administers the university library has no business in the student
union's signing credentials. The estate runs three tenants for exactly that
reason.

**The auth machinery goes into `edutap.admin_auth` from the first service, not
the second.** Configurable sign-in, the settings-driven permission map and the
`requires()` check are needed by every service with an admin API. Writing them
here and extracting them later means writing them twice and extracting them once
they have already drifted — this repository has three fresh examples of that
failure mode from the week of 2026-09-01 alone. For a security function, the
second copy is the one that carries the mistake.

## Consequences

**This service.** A new admin mount, a dependency on `edutap.admin_auth`, and a
permission declared on every management route. Tenants become read-only here and
declared in settings; `tenants:write` never enters the vocabulary. The
in-process interface lives on with a known end.

**The estate.** Every service that wants administering grows an admin API and a
second Traefik router. The signing service's htmx interface is ported after the
first slice — it holds the most dangerous material in the estate and makes a
poor first customer for a library that does not exist yet.

**What gets worse.** The in-process interface needed no token, no network hop
and no second serialisation; it sat on the same session as the routers it
mounted. Everything the new interface does crosses a boundary, and every failure
it can suffer is one the old one could not. That is the price of a UI that can
administer more than one service, and it is paid knowingly.

**What we accept for now.** Two interfaces onto the same data exist side by side
until the migration finishes. That is a state to be ended, not maintained, and
this ADR is the record that it was chosen with an end in mind.

## The way back

If the separate interface proves not to be worth it, the way back is open and
cheap for as long as `ui/app.py` exists: stop building screens, keep the
in-process interface, and drop the admin mounts. The management routers are
untouched by any of this — they are mounted a third time, not rewritten — so
nothing in the service layer has to be reverted.

That way back closes when `ui/app.py` is removed. Removing it is therefore the
decision point at which this ADR stops being reversible, and it should not
happen quietly.
