from fastapi import FastAPI
from fastapi.testclient import TestClient

from edutap.pass_builder.errors import ProblemError, install_error_handlers


def build_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise ProblemError(
            422, "missing_field", "Missing fields", fields=["person.name"]
        )

    return TestClient(app)


def test_problem_error_is_rendered_as_problem_json():
    response = build_client().get("/boom")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "urn:edutap:pass-builder:missing_field"
    assert body["title"] == "Missing fields"
    assert body["status"] == 422
    assert body["fields"] == ["person.name"]


def test_extra_kwargs_cannot_clobber_the_reserved_rfc9457_fields():
    """A caller's `**extra` key must never overwrite a reserved RFC 9457 field.

    `status`, `slug`, `title` and `detail` are formal `ProblemError`
    parameters, so they can never actually end up inside `**extra` --
    `type` is the one reserved body key that is *not* a formal parameter,
    so it is the one a caller could plausibly (if accidentally) pass
    through `**extra`. `to_dict()` applies `extra` before the RFC 9457
    fields precisely so that, if it ever does, the real `type` still wins.
    """
    error = ProblemError(422, "x", "T", type="evil")

    assert error.to_dict()["type"] == "urn:edutap:pass-builder:x"
