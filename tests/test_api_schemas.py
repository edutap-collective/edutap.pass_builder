"""Tests pinning the API request/response schemas' shape."""

import pytest
from pydantic import ValidationError

from edutap.pass_builder.models.api import CreatePassRequest, CredentialResponse


def test_credential_response_has_no_secret_fields():
    """CredentialResponse must never expose secret material."""
    fields = set(CredentialResponse.model_fields)
    forbidden_fields = (
        "private_key",
        "service_account_json",
        "ciphertext",
        "wrapped_dek",
    )
    for forbidden in forbidden_fields:
        assert forbidden not in fields


def test_credential_response_exposes_metadata():
    """CredentialResponse still carries the metadata clients need."""
    fields = set(CredentialResponse.model_fields)
    assert "pass_type_identifier" in fields
    assert "cert_fingerprint_sha256" in fields


def test_create_pass_request_requires_pass_id_and_person_uid():
    """CreatePassRequest rejects a payload missing required identifiers."""
    incomplete_payload = {"template": "student-id", "wallet_type": "APPLE_VAS"}
    with pytest.raises(ValidationError):
        CreatePassRequest(**incomplete_payload)
