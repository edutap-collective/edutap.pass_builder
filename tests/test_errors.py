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
