from fastapi.testclient import TestClient

from edutap.pass_builder.app import create_app


def test_healthz_reports_alive():
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
