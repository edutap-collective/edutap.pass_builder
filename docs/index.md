# edutap.pass_builder

A stateless FastAPI service that turns a stored pass template plus person
data into a signed Apple Wallet pass or a pushed Google Wallet object.

`edutap.pass_builder` persists templates, variants, versions, mapping rules
and signing credentials.
It does not persist issued passes: the calling service owns and stores
whatever it hands out.
The only record the service keeps of a render is an audit entry.

```{toctree}
:maxdepth: 2
:caption: Tutorial

tutorials/first-pass
```

```{toctree}
:maxdepth: 2
:caption: How-to guides

how-to/obtain-and-install-an-apple-credential
how-to/import-a-pkpasstemplate
how-to/run-the-docker-test-environment
how-to/configure-credentials-and-wwdr
```

```{toctree}
:maxdepth: 2
:caption: Reference

reference/rest-api
reference/data-model
reference/configuration
```

```{toctree}
:maxdepth: 2
:caption: Explanation

explanation/why-stateless
explanation/why-placeholders
explanation/why-immutable-versions
```
