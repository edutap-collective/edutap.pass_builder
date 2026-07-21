"""Credential set lifecycle: create, install, import, list and decrypt."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto.certificates import (
    certificate_matches_key,
    parse_apple_certificate,
    parse_service_account,
)
from ..crypto.keys import build_csr, generate_private_key
from ..errors import ProblemError
from ..models.db import CredentialSet, SecretBlob
from ..models.enums import CredentialStatus, Provider, SecretKind
from ..secrets.backend import SealedSecret, SecretBackend


def _sealed_from_blob(blob: SecretBlob) -> SealedSecret:
    """Rebuild a `SealedSecret` from the persisted `SecretBlob` fields."""
    return SealedSecret(
        ciphertext=blob.ciphertext,
        nonce=blob.nonce,
        wrapped_dek=blob.wrapped_dek,
        algo=blob.algo,
    )


class CredentialService:
    """Manages Apple and Google credential sets and their secret material."""

    def __init__(self, session: AsyncSession, backend: SecretBackend) -> None:
        """Bind the service to one session and one secret backend."""
        self._session = session
        self._backend = backend

    async def create_apple(
        self, tenant_id: UUID, label: str, common_name: str
    ) -> CredentialSet:
        """Generate a key and CSR for a new Apple credential set.

        Seals the private key, stores it as a `SecretBlob`, and creates a
        `CredentialSet` with status `key_pending` holding the CSR to submit
        to Apple.
        """
        key_pem = generate_private_key()
        csr_pem = build_csr(key_pem, common_name)

        credential_set = CredentialSet(
            tenant_id=tenant_id,
            provider=Provider.APPLE,
            label=label,
            status=CredentialStatus.KEY_PENDING,
            csr_pem=csr_pem.decode(),
        )
        self._session.add(credential_set)
        await self._session.flush()

        await self._store_secret(credential_set.id, SecretKind.PRIVATE_KEY, key_pem)
        await self._session.flush()
        return credential_set

    async def install_certificate(
        self, tenant_id: UUID, credential_id: UUID, cert_pem: bytes
    ) -> CredentialSet:
        """Attach a signed certificate to a pending Apple credential set.

        Verifies the certificate's public key matches the stored private
        key, then fills in the certificate metadata and activates the set.
        """
        credential_set = await self._load(tenant_id, credential_id)
        key_pem = await self._load_secret(credential_set.id, SecretKind.PRIVATE_KEY)

        if not certificate_matches_key(cert_pem, key_pem):
            raise ProblemError(
                409,
                "certificate_key_mismatch",
                "The certificate does not match the stored private key",
            )

        info = parse_apple_certificate(cert_pem)
        credential_set.pass_type_identifier = info.pass_type_identifier
        credential_set.team_identifier = info.team_identifier
        credential_set.organization_name = info.organization_name
        credential_set.cert_serial = info.cert_serial
        credential_set.cert_fingerprint_sha256 = info.cert_fingerprint_sha256
        credential_set.not_before = info.not_before
        credential_set.not_after = info.not_after
        credential_set.nfc_capable = info.nfc_capable
        credential_set.issuer_generation = info.issuer_generation
        credential_set.certificate_pem = cert_pem.decode()
        credential_set.status = CredentialStatus.ACTIVE
        credential_set.updated_at = datetime.now(UTC)

        self._session.add(credential_set)
        await self._session.flush()
        return credential_set

    async def import_apple(
        self, tenant_id: UUID, label: str, key_pem: bytes, cert_pem: bytes
    ) -> CredentialSet:
        """Import an existing Apple key and certificate pair as active.

        Verifies the key and certificate match before storing anything.
        """
        if not certificate_matches_key(cert_pem, key_pem):
            raise ProblemError(
                409,
                "certificate_key_mismatch",
                "The certificate does not match the given private key",
            )

        info = parse_apple_certificate(cert_pem)
        credential_set = CredentialSet(
            tenant_id=tenant_id,
            provider=Provider.APPLE,
            label=label,
            status=CredentialStatus.ACTIVE,
            pass_type_identifier=info.pass_type_identifier,
            team_identifier=info.team_identifier,
            organization_name=info.organization_name,
            cert_serial=info.cert_serial,
            cert_fingerprint_sha256=info.cert_fingerprint_sha256,
            not_before=info.not_before,
            not_after=info.not_after,
            nfc_capable=info.nfc_capable,
            issuer_generation=info.issuer_generation,
            certificate_pem=cert_pem.decode(),
        )
        self._session.add(credential_set)
        await self._session.flush()

        await self._store_secret(credential_set.id, SecretKind.PRIVATE_KEY, key_pem)
        await self._session.flush()
        return credential_set

    async def import_google(
        self,
        tenant_id: UUID,
        label: str,
        service_account_raw: bytes,
        issuer_id: str,
    ) -> CredentialSet:
        """Import a Google service account JSON as an active credential set."""
        info = parse_service_account(service_account_raw)

        credential_set = CredentialSet(
            tenant_id=tenant_id,
            provider=Provider.GOOGLE,
            label=label,
            status=CredentialStatus.ACTIVE,
            service_account_email=info.service_account_email,
            private_key_id=info.private_key_id,
            project_id=info.project_id,
            issuer_id=issuer_id,
        )
        self._session.add(credential_set)
        await self._session.flush()

        await self._store_secret(
            credential_set.id, SecretKind.SERVICE_ACCOUNT_JSON, service_account_raw
        )
        await self._session.flush()
        return credential_set

    async def list_sets(
        self,
        tenant_id: UUID,
        provider: Provider | None = None,
        expiring_within_days: int | None = None,
    ) -> list[CredentialSet]:
        """Return the tenant's credential sets, optionally filtered.

        `provider` restricts to one wallet provider; `expiring_within_days`
        restricts to sets whose `not_after` falls within that many days from
        now. Results are ordered by `not_after`.
        """
        query = select(CredentialSet).where(
            CredentialSet.tenant_id  # ty: ignore[invalid-argument-type]
            == tenant_id
        )
        if provider is not None:
            query = query.where(
                CredentialSet.provider  # ty: ignore[invalid-argument-type]
                == provider
            )
        if expiring_within_days is not None:
            deadline = datetime.now(UTC) + timedelta(days=expiring_within_days)
            query = query.where(
                CredentialSet.not_after <= deadline  # ty: ignore[unsupported-operator]
            )
        query = query.order_by(
            CredentialSet.not_after  # ty: ignore[invalid-argument-type]
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_csr(self, tenant_id: UUID, credential_id: UUID) -> str:
        """Return the stored CSR PEM for a credential set."""
        credential_set = await self._load(tenant_id, credential_id)
        if not credential_set.csr_pem:
            raise ProblemError(
                404, "credential_not_found", "No CSR stored for this credential set"
            )
        return credential_set.csr_pem

    async def open_material(self, credential_set: CredentialSet) -> bytes:
        """Decrypt and return the secret material of a credential set.

        This is the only path by which key or service account material
        leaves storage in plaintext; it is used exclusively by the render
        path and must never be exposed through any API response.
        """
        blob = await self._get_blob(credential_set.id)
        if blob is None:
            raise ProblemError(404, "credential_not_found", "No secret material stored")
        return self._backend.open(_sealed_from_blob(blob))

    async def _load(self, tenant_id: UUID, credential_id: UUID) -> CredentialSet:
        """Return the tenant-scoped credential set or raise 404."""
        query = select(CredentialSet).where(
            CredentialSet.id  # ty: ignore[invalid-argument-type]
            == credential_id,
            CredentialSet.tenant_id  # ty: ignore[invalid-argument-type]
            == tenant_id,
        )
        credential_set = (await self._session.execute(query)).scalar_one_or_none()
        if credential_set is None:
            raise ProblemError(
                404, "credential_not_found", "No such credential set for this tenant"
            )
        return credential_set

    async def _get_blob(self, credential_set_id: UUID) -> SecretBlob | None:
        """Return the secret blob linked to a credential set, if any."""
        query = select(SecretBlob).where(
            SecretBlob.credential_set_id  # ty: ignore[invalid-argument-type]
            == credential_set_id
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def _load_secret(self, credential_set_id: UUID, kind: SecretKind) -> bytes:
        """Load and decrypt the secret material for a credential set."""
        blob = await self._get_blob(credential_set_id)
        if blob is None or blob.kind != kind:
            raise ProblemError(404, "credential_not_found", "No secret material stored")
        return self._backend.open(_sealed_from_blob(blob))

    async def _store_secret(
        self, credential_set_id: UUID, kind: SecretKind, plaintext: bytes
    ) -> None:
        """Seal and persist secret material for a credential set."""
        sealed = self._backend.seal(plaintext)
        blob = SecretBlob(
            credential_set_id=credential_set_id,
            kind=kind,
            ciphertext=sealed.ciphertext,
            nonce=sealed.nonce,
            wrapped_dek=sealed.wrapped_dek,
            algo=sealed.algo,
        )
        self._session.add(blob)
