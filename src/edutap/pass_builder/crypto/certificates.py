"""Extract metadata from Apple certificates and Google service accounts."""

import json
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID, ObjectIdentifier
from pydantic import BaseModel

_NFC_EXTENSION = ObjectIdentifier("1.2.840.113635.100.6.1.26")


class AppleCertInfo(BaseModel):
    """Metadata derived from an Apple pass type certificate."""

    pass_type_identifier: str
    team_identifier: str
    organization_name: str
    cert_serial: str
    cert_fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    nfc_capable: bool
    issuer_generation: str


class GoogleServiceAccountInfo(BaseModel):
    """Metadata derived from a Google service account JSON file."""

    service_account_email: str
    private_key_id: str
    project_id: str


def _first(name: x509.Name, oid: ObjectIdentifier) -> str:
    """Return the first attribute value for the given OID, or an empty string."""
    values = name.get_attributes_for_oid(oid)
    return str(values[0].value) if values else ""


def parse_apple_certificate(pem: bytes) -> AppleCertInfo:
    """Return the metadata of an Apple pass type certificate."""
    cert = x509.load_pem_x509_certificate(pem)
    nfc_capable = True
    try:
        cert.extensions.get_extension_for_oid(_NFC_EXTENSION)
    except x509.ExtensionNotFound:
        nfc_capable = False
    return AppleCertInfo(
        pass_type_identifier=_first(cert.subject, NameOID.USER_ID),
        team_identifier=_first(cert.subject, NameOID.ORGANIZATIONAL_UNIT_NAME),
        organization_name=_first(cert.subject, NameOID.ORGANIZATION_NAME),
        cert_serial=format(cert.serial_number, "X"),
        cert_fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        nfc_capable=nfc_capable,
        issuer_generation=_first(cert.issuer, NameOID.ORGANIZATIONAL_UNIT_NAME),
    )


def certificate_matches_key(cert_pem: bytes, key_pem: bytes) -> bool:
    """Return True if the certificate's public key matches the private key."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = load_pem_private_key(key_pem, password=None)
    # RSA is the only key type used for Apple pass certificates; other key
    # types in the union (Ed25519, X25519, ...) have no public_numbers().
    cert_numbers = cert.public_key().public_numbers()  # ty: ignore[unresolved-attribute]
    key_numbers = key.public_key().public_numbers()  # ty: ignore[unresolved-attribute]
    return bool(cert_numbers == key_numbers)


def parse_service_account(raw: bytes) -> GoogleServiceAccountInfo:
    """Return the metadata of a Google service account JSON file."""
    data = json.loads(raw)
    return GoogleServiceAccountInfo(
        service_account_email=data["client_email"],
        private_key_id=data["private_key_id"],
        project_id=data["project_id"],
    )
