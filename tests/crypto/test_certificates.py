from pathlib import Path

from edutap.pass_builder.crypto.certificates import parse_apple_certificate

CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"


def test_apple_certificate_fields_are_extracted():
    info = parse_apple_certificate(CERT.read_bytes())
    assert info.pass_type_identifier == "pass.de.lmu.ub"  # noqa: S105
    assert info.team_identifier == "JG943677ZY"
    assert info.organization_name.startswith("Ludwig-Maximilians")
    assert info.issuer_generation == "G4"
    assert info.nfc_capable is True
    assert info.not_after.year == 2026
