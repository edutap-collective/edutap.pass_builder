"""Render passes: resolve spec, project fields, bind, build/push, audit.

This is the privacy-critical runtime path. It requests only the fields a
template actually maps (data minimisation), never logs or audits a field
*value*, and writes an audit entry on both success and failure.
"""

import copy
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from edutap.data_models.vocabulary import IssuanceState
from edutap.db_definitions.public.tables import PassState
from edutap.wallet_google import api as wallet_google_api
from edutap.wallet_google.exceptions import ObjectAlreadyExistsException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext
from ..clients.data_provider import DataProviderClient
from ..engine.apple_apply import NfcPayloadTooLongError, apply_apple
from ..engine.apple_build import build_apple
from ..engine.binding import MissingFieldsError, bind, required_fields
from ..engine.google_apply import apply_google
from ..engine.google_build import build_google_object, google_object_id
from ..engine.spec import BoundValue, RenderSpec, RuleSpec
from ..errors import ProblemError
from ..models.db import CredentialSet, Template, TemplateVariant, TemplateVersion
from ..models.enums import (
    APPLE_WALLET_TYPES,
    GOOGLE_WALLET_TYPES,
    SUPPORTED_WALLET_TYPES,
    ValueType,
    VersionStatus,
    WalletType,
)
from ..settings import Settings
from .audit import write_audit, write_audit_durable
from .credentials import CredentialService
from .templates import TemplateService

# Generated placeholder values for `preview`, one per `ValueType`. `preview`
# never calls the data provider, so any rule not covered by `sample_data`
# gets one of these instead of a real value.
_SAMPLE_PLACEHOLDERS: dict[ValueType, str | bytes] = {
    ValueType.TEXT: "Sample Text",
    ValueType.DATE: "2024-01-01",
    ValueType.NUMBER: "1",
    ValueType.BOOLEAN: "true",
    ValueType.URI: "https://example.org/sample",
    ValueType.IMAGE: b"\x89PNG",
}

_GOOGLE_OBJECT_MODEL = "GenericObject"

#: What a withdrawn Google Wallet object is set to.
#:
#: `EXPIRED` rather than `INACTIVE` or `COMPLETED`: the Wallet app renders all
#: three the same way -- greyed out and out of the pass list -- and of the three
#: only `EXPIRED` says why. The distinction is ours to make and nobody else's,
#: so it should be the one that reads correctly in an audit entry a year later.
_GOOGLE_STATE_WITHDRAWN = "EXPIRED"


class RenderResult(BaseModel):
    """The outcome of rendering one pass."""

    wallet_type: WalletType
    pkpass: bytes | None = None
    object_id: str | None = None
    class_id: str | None = None
    template_version: int
    variant: str
    credential_set: str | None = None
    """The credential set's label, if one was used to sign or push."""


class SupportsGoogleApi(Protocol):
    """The subset of `edutap.wallet_google.api` the render path depends on.

    A `Protocol` rather than the concrete module so unit tests can inject a
    fake that never touches the network.
    """

    def new(self, name: str, data: dict[str, Any]) -> Any:
        """Build a registered Google Wallet model instance from plain data."""
        ...

    async def acreate(self, data: Any, *, credentials: dict | None = None) -> Any:
        """Create a Google Wallet object."""
        ...

    async def aupdate(self, data: Any, *, credentials: dict | None = None) -> Any:
        """Update a Google Wallet object."""
        ...

    def save_link(self, models: list[Any], *, credentials: dict | None = None) -> str:
        """Return a signed "save to wallet" link for the given references."""
        ...


class SupportsImageFetch(Protocol):
    """What the render path needs of `clients.image_service.ImageServiceClient`.

    A `Protocol` rather than the concrete class, for the same reason as
    `SupportsGoogleApi` above: a unit test can hand in a fake that reaches no
    network and still type-check.
    """

    async def fetch(self, url: str) -> bytes:
        """Return the bytes behind an image reference."""
        ...


def _elapsed_ms(start: float) -> int:
    """Return the milliseconds elapsed since `start` (a `time.monotonic()`)."""
    return int((time.monotonic() - start) * 1000)


def _placeholder_for(rule: RuleSpec) -> str | bytes:
    """Return a generated placeholder value for a rule with no sample data."""
    return _SAMPLE_PLACEHOLDERS.get(rule.value_type, f"<{rule.source_field}>")


def _require_supported(wallet_type: WalletType) -> None:
    """Refuse a wallet type this service does not implement.

    The single gate: every build goes through `_render`, and `save_link` and
    `deactivate_pass` carry their own, narrower checks for the same reason.
    """
    if wallet_type not in SUPPORTED_WALLET_TYPES:
        supported = ", ".join(sorted(w.value for w in SUPPORTED_WALLET_TYPES))
        raise ProblemError(
            501,
            "wallet_type_not_supported",
            f"This service does not build {wallet_type.value} passes",
            f"Supported wallet types are: {supported}. Access and Identity "
            "credentials require secure element provisioning, which this service "
            "does not do; they are built elsewhere.",
        )


async def resolve_image_references(
    bound: list[BoundValue], images: SupportsImageFetch | None
) -> list[BoundValue]:
    """Replace every `IMAGE` reference with the bytes it points at.

    THE APPLE PATH ONLY, and the asymmetry is the platforms', not ours: a
    `.pkpass` is a bundle and carries the picture as a file inside it, so the
    reference has to become bytes before the bundle is built. A Google object
    carries images by URL and the wallet fetches them itself, so there the
    reference already *is* the right value -- fetching it would put a copy of a
    person's photograph somewhere Google never reads.

    A value that is already bytes is left alone. Only `preview` produces one,
    and a preview reaches no network.

    A free function rather than a method: it needs nothing of the service but
    the client, and that is what makes it testable without one.
    """
    resolved: list[BoundValue] = []
    for item in bound:
        if item.rule.value_type != ValueType.IMAGE or isinstance(item.value, bytes):
            resolved.append(item)
            continue
        if images is None:
            # A deployment problem, not a caller's: the template maps an image
            # and nothing was wired to fetch it. Saying so beats building a
            # pass with a silent hole where the picture goes.
            raise ProblemError(
                500,
                "image_service_not_configured",
                "Template maps an image but no image service is configured",
            )
        resolved.append(
            item.model_copy(update={"value": await images.fetch(item.value)})
        )
    return resolved


class RenderService:
    """Turns (template, person_uid, wallet_type) into a delivered pass."""

    def __init__(
        self,
        session: AsyncSession,
        templates: TemplateService,
        credentials: CredentialService,
        data_provider: DataProviderClient,
        *,
        images: SupportsImageFetch | None = None,
        google_api: SupportsGoogleApi | None = None,
        apple_sign: Callable[[object], None] | None = None,
        wwdr_certificate_path: Path = Settings.model_fields[
            "wwdr_certificate_path"
        ].default,
    ) -> None:
        """Bind the service to its collaborators.

        `images` resolves an `IMAGE` mapping rule's reference to bytes on the
        Apple path. Optional so a test that maps no image needs no fake for
        it; a template that *does* map one and finds it unset gets a 500
        rather than a pass with a hole where the picture goes.

        `google_api` and `apple_sign` are test-only overrides: `google_api`
        replaces the real `edutap.wallet_google.api` module so unit tests
        never touch the network, and `apple_sign` replaces the signer this
        service would otherwise build from `credentials` so unit tests never
        sign with a real key.

        `wwdr_certificate_path` is the Apple WWDR intermediate certificate
        used to sign, taken from `Settings.wwdr_certificate_path` by the
        caller (see `dependencies.get_render_service`) -- resolved here via
        constructor injection, like every other settings-derived value this
        service's collaborators need, rather than this service reaching for
        `get_settings()` itself.
        """
        self._session = session
        self._templates = templates
        self._credentials = credentials
        self._data_provider = data_provider
        self._images = images
        self._google_api: SupportsGoogleApi = google_api or wallet_google_api
        self._apple_sign_override = apple_sign
        self._wwdr_certificate_path = wwdr_certificate_path

    async def create_pass(
        self,
        auth: AuthContext,
        *,
        pass_id: str,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        person_uid: str,
        version_number: int | None = None,
        request_id: str | None = None,
    ) -> RenderResult:
        """Render and deliver a new pass, requesting only the mapped fields."""
        return await self._render(
            auth,
            pass_id=pass_id,
            template_key=template_key,
            wallet_type=wallet_type,
            variant_key=variant_key,
            person_uid=person_uid,
            version_number=version_number,
            request_id=request_id,
            action="pass.create",
            is_update=False,
        )

    async def update_pass(
        self,
        auth: AuthContext,
        *,
        pass_id: str,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        person_uid: str,
        version_number: int | None = None,
        request_id: str | None = None,
    ) -> RenderResult:
        """Re-render and re-deliver an existing pass.

        The variant may differ from the one used to create the pass (for
        example a Google class switch); the resolved template version and
        variant are read fresh on every call, just like `create_pass`.
        """
        return await self._render(
            auth,
            pass_id=pass_id,
            template_key=template_key,
            wallet_type=wallet_type,
            variant_key=variant_key,
            person_uid=person_uid,
            version_number=version_number,
            request_id=request_id,
            action="pass.update",
            is_update=True,
        )

    async def save_link(
        self,
        auth: AuthContext,
        *,
        pass_id: str,
        template_key: str,
        wallet_type: WalletType = WalletType.GOOGLE_ST,
        variant_key: str | None = None,
        version_number: int | None = None,
        request_id: str | None = None,
    ) -> str:
        """Return a Google "save to wallet" link for an already-pushed object.

        Apple passes have no equivalent web link in this design -- they are
        distributed as a signed `.pkpass` file -- so only Google wallet types
        are supported here. Writes a `pass.save_link` audit entry on both
        success and failure, mirroring `_render` -- `pass_id` is recorded as
        `subject_ref`, never a field value.
        """
        if wallet_type not in GOOGLE_WALLET_TYPES:
            raise ProblemError(
                501,
                "wallet_type_not_supported",
                "Save links are only implemented for Google Wallet passes",
                "An Apple pass is distributed as a signed .pkpass file and has "
                "no equivalent web link in this design.",
            )
        start = time.monotonic()
        request_id = request_id or str(uuid4())
        template_id: UUID | None = None
        variant_id: UUID | None = None
        version_id: UUID | None = None
        try:
            _template, variant, _version = await self._resolve(
                auth.tenant_id, template_key, wallet_type, variant_key, version_number
            )
            template_id, variant_id, version_id = (
                _template.id,
                variant.id,
                _version.id,
            )
            credential_set = await self._load_credential_set(
                auth.tenant_id, variant.credential_set_id
            )
            if credential_set is None or credential_set.issuer_id is None:
                raise ProblemError(
                    409,
                    "google_credentials_missing",
                    "No Google credential set configured for this variant",
                )
            if variant.google_class_id is None:
                raise ProblemError(
                    409,
                    "google_class_not_configured",
                    "Variant has no Google class id configured",
                )
            credentials = await self._open_google_credentials(credential_set)
            object_id = google_object_id(credential_set.issuer_id, pass_id)
            reference = self._google_api.new(
                "Reference", {"id": object_id, "model_name": _GOOGLE_OBJECT_MODEL}
            )
            link = self._google_api.save_link([reference], credentials=credentials)
        except ProblemError as exc:
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action="pass.save_link",
                error_code=exc.slug,
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=pass_id,
                fields=[],
            )
            raise
        except Exception as exc:
            # Mirrors `_render`: any unexpected error still leaves an audit
            # trail, with only a generic slug -- never the original message,
            # which could carry secret material.
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action="pass.save_link",
                error_code="internal_error",
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=pass_id,
                fields=[],
            )
            raise ProblemError(500, "internal_error", "Internal error") from exc

        await write_audit(
            self._session,
            tenant_id=auth.tenant_id,
            request_id=request_id,
            actor_client_id=auth.client_id,
            actor_principal=auth.principal,
            action="pass.save_link",
            outcome="success",
            error_code=None,
            duration_ms=_elapsed_ms(start),
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=pass_id,
            requested_fields=[],
        )
        return link

    async def deactivate_pass(
        self,
        auth: AuthContext,
        *,
        pass_id: str,
        template_key: str,
        wallet_type: WalletType = WalletType.GOOGLE_ST,
        variant_key: str | None = None,
        version_number: int | None = None,
        request_id: str | None = None,
    ) -> tuple[str, str]:
        """Withdraw an issued pass. Returns `(object_id, state)`.

        GOOGLE ONLY, and the restriction is one of reach rather than of taste.
        Withdrawing a Google pass is a state change on an object this service
        owns and can reach: `state -> EXPIRED`, sent as a PATCH. An Apple pass
        is withdrawn by putting `voided` on it and getting the DEVICE to fetch
        the new version -- and the fetching is done by the VAS web service,
        which this one neither owns nor can reach. Rendering voided bytes here
        and handing them back would look like a withdrawal while the pass on
        the phone stayed valid, so the request is refused with a 501 instead.

        No `person_uid` and no re-rendering: the object id follows from the
        issuer id and `pass_id`, and nothing but the state is touched. The
        PATCH body is `{id, classId, state}` -- verified against
        `_prepare_update`, which drops the model defaults that are not set,
        `genericType` among them. A full replacement would have to carry the
        whole object, and every field it failed to carry would be erased.

        Idempotent by nature: setting EXPIRED on an already expired object
        changes nothing and still answers 200. A withdrawal that fails the
        second time would invite callers to skip retries.

        Writes a `pass.deactivate` audit entry on success and on failure, the
        same as `save_link` and `_render`. `pass_id` is recorded as
        `subject_ref`, never a field value.
        """
        start = time.monotonic()
        request_id = request_id or str(uuid4())
        template_id: UUID | None = None
        variant_id: UUID | None = None
        version_id: UUID | None = None
        try:
            if wallet_type not in GOOGLE_WALLET_TYPES:
                # INSIDE the `try`, unlike the same check in `save_link`, and
                # deliberately: a refused withdrawal is exactly what somebody
                # asks about later ("did anyone try to revoke this pass?").
                # Raised as a `ProblemError` rather than `NotImplementedError`
                # so it travels through the installed handler and reaches the
                # caller as a 501 problem document. A bare
                # `NotImplementedError` has no handler here and would arrive as
                # a 500 -- "our fault, try again" -- for something that is
                # neither our fault nor worth retrying.
                raise ProblemError(
                    501,
                    "wallet_type_not_supported",
                    "Withdrawing is only implemented for Google Wallet passes",
                    "An Apple pass is withdrawn by delivering a voided version "
                    "to the device, which this service does not do.",
                )
            _template, variant, _version = await self._resolve(
                auth.tenant_id, template_key, wallet_type, variant_key, version_number
            )
            template_id, variant_id, version_id = (
                _template.id,
                variant.id,
                _version.id,
            )
            credential_set = await self._load_credential_set(
                auth.tenant_id, variant.credential_set_id
            )
            if credential_set is None or credential_set.issuer_id is None:
                raise ProblemError(
                    409,
                    "google_credentials_missing",
                    "No Google credential set configured for this variant",
                )
            if variant.google_class_id is None:
                raise ProblemError(
                    409,
                    "google_class_not_configured",
                    "Variant has no Google class id configured",
                )
            credentials = await self._open_google_credentials(credential_set)
            object_id = google_object_id(credential_set.issuer_id, pass_id)
            obj = self._google_api.new(
                _GOOGLE_OBJECT_MODEL,
                {
                    "id": object_id,
                    "classId": variant.google_class_id,
                    "state": _GOOGLE_STATE_WITHDRAWN,
                },
            )
            await self._google_api.aupdate(obj, credentials=credentials)
        except ProblemError as exc:
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action="pass.deactivate",
                error_code=exc.slug,
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=pass_id,
                fields=[],
            )
            raise
        except Exception as exc:
            # Mirrors `_render` and `save_link`: any unexpected error still
            # leaves an audit trail, with only a generic slug -- never the
            # original message, which could carry secret material.
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action="pass.deactivate",
                error_code="internal_error",
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=pass_id,
                fields=[],
            )
            raise ProblemError(500, "internal_error", "Internal error") from exc

        await write_audit(
            self._session,
            tenant_id=auth.tenant_id,
            request_id=request_id,
            actor_client_id=auth.client_id,
            actor_principal=auth.principal,
            action="pass.deactivate",
            outcome="success",
            error_code=None,
            duration_ms=_elapsed_ms(start),
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=pass_id,
            requested_fields=[],
        )
        return object_id, _GOOGLE_STATE_WITHDRAWN

    async def preview(
        self,
        auth: AuthContext,
        *,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        version_number: int | None,
        sample_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a resolved pass/object preview built from sample data.

        Never calls the data provider and writes no audit entry: this is a
        design-time helper, not a rendering of a real person's pass. Any
        mapped field missing from `sample_data` is filled with a generated
        placeholder for its `value_type`.
        """
        spec = await self._templates.build_render_spec(
            auth.tenant_id, template_key, wallet_type, variant_key, version_number
        )
        data: dict[str, Any] = dict(sample_data)
        for rule in spec.rules:
            data.setdefault(rule.source_field, _placeholder_for(rule))
        bound = bind(spec.rules, data)
        bound_fields = [item.rule.source_field for item in bound]

        if spec.wallet_type in APPLE_WALLET_TYPES:
            # Deep, not shallow: `_set_field`/`_set_pointer` mutate nested
            # dicts (a field entry, a JSON-pointer target's parent) in
            # place, not just the top-level dict `dict(...)` would copy --
            # a shallow copy here would still share and mutate those
            # nested structures with the ORM-managed `spec.pass_json`,
            # exactly like `build_apple` deep-copies for the same reason.
            resolved, _assets = apply_apple(
                copy.deepcopy(spec.pass_json or {}), dict(spec.assets), bound
            )
            return {"pass_json": resolved, "bound_fields": bound_fields}
        if spec.wallet_type in GOOGLE_WALLET_TYPES:
            # `apply_google` (via `resolve_placeholders`) already returns a
            # brand new structure rather than mutating its input -- unlike
            # `apply_apple`, it needs no defensive copy here.
            resolved_object = apply_google(dict(spec.object_json or {}), bound)
            return {"object_json": resolved_object, "bound_fields": bound_fields}
        raise ProblemError(
            400, "unsupported_wallet_type", f"Unsupported wallet type: {wallet_type}"
        )

    async def _render(
        self,
        auth: AuthContext,
        *,
        pass_id: str,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        person_uid: str,
        version_number: int | None,
        request_id: str | None,
        action: str,
        is_update: bool,
    ) -> RenderResult:
        """Shared body of `create_pass`/`update_pass`: resolve, bind, build, audit."""
        # BEFORE ANYTHING ELSE, and before the audit entry exists: a wallet type this
        # service cannot build is not a failed render, it is a request that was never
        # going to happen. Recording it as an attempt would put a row in the audit log
        # for work nobody did.
        #
        # 501 rather than 400: the request is well formed and the wallet type is real
        # -- Access and Identity are credential technologies involving secure element
        # provisioning, which this service does not do at all. A 400 would tell the
        # caller to fix something that is not wrong, and a 500 would claim it was our
        # fault and worth retrying.
        _require_supported(wallet_type)
        start = time.monotonic()
        request_id = request_id or str(uuid4())
        fields: list[str] = []
        template_id: UUID | None = None
        variant_id: UUID | None = None
        version_id: UUID | None = None
        try:
            template, variant, version = await self._resolve(
                auth.tenant_id, template_key, wallet_type, variant_key, version_number
            )
            template_id, variant_id, version_id = template.id, variant.id, version.id

            spec = await self._templates.build_render_spec(
                auth.tenant_id, template_key, wallet_type, variant_key, version_number
            )
            fields = required_fields(spec.rules)
            data = await self._data_provider.fetch_fields(person_uid, fields)
            bound = self._bind_or_raise(spec.rules, data)

            result = await self._build_and_deliver(
                auth.tenant_id,
                spec,
                variant,
                version,
                bound,
                pass_id,
                is_update=is_update,
            )
        except ProblemError as exc:
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action=action,
                error_code=exc.slug,
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=person_uid,
                fields=fields,
            )
            raise
        except Exception as exc:
            # Anything unexpected -- a JSON decode error, a network failure
            # from the Google push, a SQLAlchemy error -- must still leave an
            # audit trail. The original message is never audited or surfaced
            # (it could carry secret/PII material); only a generic slug is.
            await self._write_error_audit(
                auth=auth,
                request_id=request_id,
                action=action,
                error_code="internal_error",
                start=start,
                template_id=template_id,
                variant_id=variant_id,
                version_id=version_id,
                wallet_type=wallet_type,
                person_uid=person_uid,
                fields=fields,
            )
            raise ProblemError(500, "internal_error", "Internal error") from exc

        await write_audit(
            self._session,
            tenant_id=auth.tenant_id,
            request_id=request_id,
            actor_client_id=auth.client_id,
            actor_principal=auth.principal,
            action=action,
            outcome="success",
            error_code=None,
            duration_ms=_elapsed_ms(start),
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=person_uid,
            requested_fields=fields,
        )
        return result

    async def _write_error_audit(
        self,
        *,
        auth: AuthContext,
        request_id: str,
        action: str,
        error_code: str,
        start: float,
        template_id: UUID | None,
        variant_id: UUID | None,
        version_id: UUID | None,
        wallet_type: WalletType,
        person_uid: str,
        fields: list[str],
    ) -> None:
        """Write one `outcome="error"` audit entry for `_render`'s except clauses.

        Written durably (see `write_audit_durable`), on its own connection,
        so it survives the request session's rollback once the exception
        this is called from finishes propagating out to `get_session`.
        """
        await write_audit_durable(
            self._session,
            tenant_id=auth.tenant_id,
            request_id=request_id,
            actor_client_id=auth.client_id,
            actor_principal=auth.principal,
            action=action,
            outcome="error",
            error_code=error_code,
            duration_ms=_elapsed_ms(start),
            template_id=template_id,
            variant_id=variant_id,
            version_id=version_id,
            wallet_type=wallet_type,
            subject_ref=person_uid,
            requested_fields=fields,
        )

    @staticmethod
    def _bind_or_raise(rules: list[RuleSpec], data: dict[str, Any]) -> list[BoundValue]:
        """Bind the rules against fetched data, translating missing fields."""
        try:
            return bind(rules, data)
        except MissingFieldsError as exc:
            raise ProblemError(
                422,
                "missing_field",
                "One or more required fields were not returned by the data provider",
                fields=exc.fields,
            ) from exc

    async def fetch_apple_pass(
        self,
        auth: AuthContext,
        *,
        pass_type_identifier: str,
        serial_number: str,
        request_id: str | None = None,
    ) -> RenderResult:
        """Rebuild an issued Apple pass from Apple's key alone.

        THE DELIVERY PATH. `wallet_apple_vas_web_service` holds registrations
        and nothing else -- it knows no person, no template and no validity,
        which is what makes it reusable at another institution -- so it asks
        for the current pass by `passTypeIdentifier` and `serialNumber`.

        THIS SERVICE STILL KEEPS NO PASS REGISTER. It does not learn that a
        pass exists, does not record one, and does not track its life; it reads
        `public.pass_state`, which the pass-state consumer owns and writes, to
        recover the two things Apple's key cannot carry: which person the pass
        is for and which template it was built from. Reading a table in the
        shared contract schema is what that schema is for. The distinction is
        worth holding on to -- it is the same one that makes `deactivate` a
        `POST` rather than a `DELETE`.

        Rebuilt rather than stored: the pass a device fetches is the pass the
        current template version and the person's current data produce. A
        stored copy would be a second truth with an unbounded staleness and a
        personal pass sitting at rest.

        `pass_type_identifier` is checked against the variant's credential set,
        which carries the identifier parsed out of the signing certificate. It
        is a guard rather than a lookup key: the tenant already comes from the
        token, and without the check a caller could fetch any serial number of
        that tenant under any pass type it liked.
        """
        state = (
            await self._session.execute(
                select(PassState).where(
                    PassState.pass_id  # ty: ignore[invalid-argument-type]
                    == serial_number
                )
            )
        ).scalar_one_or_none()
        if state is None:
            raise ProblemError(404, "pass_not_found", "No such pass")
        if state.issuance_state == IssuanceState.REVOKED:
            # 410 and not 404: the pass existed and was withdrawn, and the
            # difference matters to the caller -- a device asking for a revoked
            # pass should stop asking, while a 404 invites a retry.
            raise ProblemError(410, "pass_revoked", "This pass has been withdrawn")
        if state.wallet_type not in APPLE_WALLET_TYPES:
            raise ProblemError(
                404, "pass_not_found", "No Apple pass with that serial number"
            )

        template, variant, _version = await self._resolve(
            auth.tenant_id,
            state.pass_template,
            state.wallet_type,
            state.pass_template_variant,
            None,
        )
        await self._require_pass_type(auth.tenant_id, variant, pass_type_identifier)

        return await self._render(
            auth,
            pass_id=serial_number,
            template_key=template.key,
            wallet_type=state.wallet_type,
            variant_key=state.pass_template_variant,
            person_uid=state.person_uid,
            version_number=None,
            request_id=request_id,
            action="pass.fetch",
            is_update=False,
        )

    async def _require_pass_type(
        self, tenant_id: UUID, variant: TemplateVariant, expected: str
    ) -> None:
        """Refuse a serial number fetched under the wrong pass type.

        `404` rather than `403`, and the same answer as an unknown serial
        number: a caller must not be able to learn which serial numbers exist
        under a pass type it is not asking about.
        """
        credential_set = await self._load_credential_set(
            tenant_id, variant.credential_set_id
        )
        if credential_set is None or credential_set.pass_type_identifier != expected:
            raise ProblemError(
                404, "pass_not_found", "No Apple pass with that serial number"
            )

    async def _build_and_deliver(
        self,
        tenant_id: UUID,
        spec: RenderSpec,
        variant: TemplateVariant,
        version: TemplateVersion,
        bound: list[BoundValue],
        pass_id: str,
        *,
        is_update: bool,
    ) -> RenderResult:
        """Build the platform payload and deliver it (sign, or push to Google)."""
        if spec.wallet_type in APPLE_WALLET_TYPES:
            bound = await resolve_image_references(bound, self._images)
            sign = await self._apple_signer(tenant_id, variant.credential_set_id)
            try:
                pkpass = build_apple(spec, bound, serial_number=pass_id, sign=sign)
            except NfcPayloadTooLongError as exc:
                # Never include the payload value itself -- only its length
                # is on the exception, and even that stays out of the
                # message surfaced to the caller (spec section 6).
                raise ProblemError(
                    422,
                    "nfc_payload_too_long",
                    "NFC payload exceeds the 64 character limit",
                ) from exc
            credential_set = await self._load_credential_set(
                tenant_id, variant.credential_set_id
            )
            return RenderResult(
                wallet_type=WalletType.APPLE_VAS,
                pkpass=pkpass,
                template_version=version.number,
                variant=variant.key,
                credential_set=credential_set.label if credential_set else None,
            )

        if spec.wallet_type in GOOGLE_WALLET_TYPES:
            if spec.issuer_id is None:
                raise ProblemError(
                    409,
                    "google_credentials_missing",
                    "No Google credential set configured for this variant",
                )
            if variant.google_class_id is None:
                raise ProblemError(
                    409,
                    "google_class_not_configured",
                    "Variant has no Google class id configured",
                )
            credential_set = await self._load_credential_set(
                tenant_id, variant.credential_set_id
            )
            credentials = await self._open_google_credentials(credential_set)

            object_id = google_object_id(spec.issuer_id, pass_id)
            obj = build_google_object(spec, bound, object_id, variant.google_class_id)
            model = self._google_api.new(_GOOGLE_OBJECT_MODEL, obj)
            if is_update:
                await self._google_api.aupdate(model, credentials=credentials)
            else:
                try:
                    await self._google_api.acreate(model, credentials=credentials)
                except ObjectAlreadyExistsException:
                    # Idempotent create: the object is already there, which
                    # is the desired end state.
                    pass
            return RenderResult(
                wallet_type=WalletType.GOOGLE_ST,
                object_id=object_id,
                class_id=variant.google_class_id,
                template_version=version.number,
                variant=variant.key,
                credential_set=credential_set.label if credential_set else None,
            )

        raise ProblemError(
            400,
            "unsupported_wallet_type",
            f"Unsupported wallet type: {spec.wallet_type}",
        )

    async def _apple_signer(
        self, tenant_id: UUID, credential_set_id: UUID | None
    ) -> Callable[[object], None]:
        """Return the callable that signs a `PkPass` in place.

        Built from `CredentialService.open_material` (the private key), the
        credential set's stored certificate, and `self._wwdr_certificate_path`
        (from `Settings.wwdr_certificate_path`) for the Apple WWDR
        intermediate.

        Signs via `PkPass.sign_direct(private_key, certificate, wwdr)`
        (the model method, not `edutap.wallet_apple.api.sign_direct`):
        `api.sign_direct` ignores any WWDR we hand it and always loads its
        own WWDR from a *separate* `edutap.wallet_apple.settings.Settings`
        instance (env prefix `EDUTAP_WALLET_APPLE_`), which would leave our
        `wwdr_certificate_path` setting dead. Calling the model method
        directly lets our setting actually control which WWDR certificate
        is used, while still handing over the key/certificate as in-memory
        bytes rather than writing secret material to disk. Unit tests
        bypass this entirely via the `apple_sign` constructor override.
        """
        if self._apple_sign_override is not None:
            return self._apple_sign_override
        credential_set = await self._load_credential_set(tenant_id, credential_set_id)
        if credential_set is None or not credential_set.certificate_pem:
            raise ProblemError(
                409,
                "apple_credentials_missing",
                "No Apple credential set configured for this variant",
            )
        key_pem = await self._credentials.open_material(credential_set)
        cert_pem = credential_set.certificate_pem.encode()
        wwdr_pem = self._wwdr_certificate_path.read_bytes()

        def sign(pkpass: object) -> None:
            pkpass.sign_direct(key_pem, cert_pem, wwdr_pem)  # ty: ignore[unresolved-attribute]

        return sign

    async def _open_google_credentials(
        self, credential_set: CredentialSet | None
    ) -> dict[str, Any] | None:
        """Return the decrypted Google service account as a dict, if any."""
        if credential_set is None:
            return None
        material = await self._credentials.open_material(credential_set)
        return json.loads(material)

    async def _load_credential_set(
        self, tenant_id: UUID, credential_set_id: UUID | None
    ) -> CredentialSet | None:
        """Return the tenant-scoped credential set for an id, or `None` if unset.

        Tenant-scoped defense in depth on the key/cert path: a variant's
        `credential_set_id` is trusted data, but this still refuses to hand
        back a credential set belonging to a different tenant. Raises
        `ProblemError(404, "credential_not_found")` rather than silently
        returning `None` when an id is set but does not resolve for this
        tenant, so a stale or mismatched id fails loudly instead of falling
        through to a generic "not configured" path.
        """
        if credential_set_id is None:
            return None
        credential_set = (
            await self._session.execute(
                select(CredentialSet).where(
                    CredentialSet.id  # ty: ignore[invalid-argument-type]
                    == credential_set_id,
                    CredentialSet.tenant_id  # ty: ignore[invalid-argument-type]
                    == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if credential_set is None:
            raise ProblemError(404, "credential_not_found", "Credential not found")
        return credential_set

    async def _resolve(
        self,
        tenant_id: UUID,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        version_number: int | None,
    ) -> tuple[Template, TemplateVariant, TemplateVersion]:
        """Resolve template/variant/version rows, tenant-scoped.

        `TemplateService.build_render_spec` (Task 14) resolves the same
        triple internally but only returns the assembled `RenderSpec`, not
        the rows themselves. The render path needs the rows too --
        `variant.credential_set_id`/`google_class_id` for signing/pushing,
        and all three ids for the audit entry -- so this mirrors that
        resolution here rather than changing Task 14's signature.
        """
        template = (
            await self._session.execute(
                select(Template).where(
                    Template.tenant_id  # ty: ignore[invalid-argument-type]
                    == tenant_id,
                    Template.key  # ty: ignore[invalid-argument-type]
                    == template_key,
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise ProblemError(
                404, "template_not_found", "No such template for this tenant"
            )

        variant_query = select(TemplateVariant).where(
            TemplateVariant.template_id  # ty: ignore[invalid-argument-type]
            == template.id,
            TemplateVariant.wallet_type  # ty: ignore[invalid-argument-type]
            == wallet_type,
        )
        if variant_key is not None:
            variant_query = variant_query.where(
                TemplateVariant.key  # ty: ignore[invalid-argument-type]
                == variant_key
            )
        else:
            variant_query = variant_query.where(
                TemplateVariant.is_default.is_(True)  # ty: ignore[unresolved-attribute]
            )
        variant = (await self._session.execute(variant_query)).scalar_one_or_none()
        if variant is None:
            raise ProblemError(404, "variant_not_found", "No such template variant")

        version_query = select(TemplateVersion).where(
            TemplateVersion.variant_id  # ty: ignore[invalid-argument-type]
            == variant.id
        )
        if version_number is not None:
            version_query = version_query.where(
                TemplateVersion.number  # ty: ignore[invalid-argument-type]
                == version_number
            )
        else:
            version_query = version_query.where(
                TemplateVersion.status  # ty: ignore[invalid-argument-type]
                == VersionStatus.PUBLISHED
            )
        version = (await self._session.execute(version_query)).scalar_one_or_none()
        if version is None:
            raise ProblemError(404, "version_not_found", "No such template version")

        return template, variant, version
