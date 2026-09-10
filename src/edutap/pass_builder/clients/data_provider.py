"""HTTP client for edutap.data_provider with field projection."""

import json
from typing import Any

import httpx
from edutap.data_models.vocabulary import FieldKind
from pydantic import BaseModel

from ..errors import ProblemError
from ..models.enums import ValueType

#: What each of the provider's field kinds means for a mapping rule.
#:
#: The two vocabularies answer different questions. `FieldKind` says what a
#: field is GOOD FOR -- it may serve as an NFC payload, a barcode message, a
#: date -- and a field carries several kinds at once. `ValueType` says what a
#: single rule BINDS. The translation therefore fans out, it does not reduce:
#: a field that is STRING and DATETIME may honestly be bound as text or as a
#: date, and rejecting either would be wrong.
#:
#: `NUMBER` and `BOOLEAN` are unreachable from here. The provider has no kind
#: that means "a number", so no catalogue field can currently be bound as one.
#: That is the provider's vocabulary talking, not an omission in this table.
_KIND_VALUE_TYPES: dict[FieldKind, ValueType] = {
    FieldKind.STRING: ValueType.TEXT,
    FieldKind.TEXT: ValueType.TEXT,
    FieldKind.NFC: ValueType.TEXT,
    FieldKind.BARCODE: ValueType.TEXT,
    FieldKind.DATETIME: ValueType.DATE,
    FieldKind.LINK: ValueType.URI,
    FieldKind.IMAGE: ValueType.IMAGE,
}

#: Which of the accepted types is the field's primary one, most general first.
#:
#: The primary type is what the cache row and the pass designer show; the full
#: set is what a rule is validated against. Text wins whenever it is available
#: because every wallet can render a value as text, so the most general answer
#: is the least surprising one to see in a field list.
_PRIMARY_PRECEDENCE: tuple[ValueType, ...] = (
    ValueType.TEXT,
    ValueType.DATE,
    ValueType.IMAGE,
    ValueType.URI,
)


class CatalogueField(BaseModel):
    """One field the data provider can deliver.

    Parsed from what `edutap.data_provider` actually sends: `key`, `kinds`,
    `derived` and `description`. It sends no `value_type`, no `label` and no
    `required` -- those are this service's own view of the field, derived here
    or defaulted.

    `kinds` is a list of plain strings rather than `list[FieldKind]` on
    purpose. A kind this service does not know yet must not take the whole
    catalogue down: the field still parses and keeps whatever its other kinds
    allow. That is the failure this class exists to prevent -- see
    `test_catalogue_parses_what_the_provider_actually_sends`.
    """

    key: str
    kinds: list[str] = []
    derived: bool = False
    label: str | None = None
    required: bool = False
    description: str | None = None

    @property
    def accepted_value_types(self) -> set[ValueType]:
        """Every value type a rule may legitimately bind this field as.

        A kind can be unknown here in two ways and both have to degrade alike. An
        unknown *string* never resolves to a `FieldKind`. But a kind that IS a
        `FieldKind` -- because `edutap.data_models` gained a member and this table has
        not caught up -- resolves fine, and a direct lookup on it would raise
        `KeyError` for the whole catalogue at the moment the cache is refreshed. So the
        mapping is consulted with `.get` and an unmapped kind is skipped like an
        unknown one.
        """
        types = {
            value_type
            for raw in self.kinds
            if (kind := _as_field_kind(raw)) is not None
            and (value_type := _KIND_VALUE_TYPES.get(kind)) is not None
        }
        return types or {ValueType.TEXT}

    @property
    def value_type(self) -> ValueType:
        """The primary type, for the cache row and the field list."""
        accepted = self.accepted_value_types
        return next(
            (candidate for candidate in _PRIMARY_PRECEDENCE if candidate in accepted),
            ValueType.TEXT,
        )


def _as_field_kind(raw: str) -> FieldKind | None:
    """Return the known kind for `raw`, or None if this service does not know it."""
    try:
        return FieldKind(raw)
    except ValueError:
        return None


class DataProviderClient:
    """Fetch projected person data and the field catalogue.

    The httpx.AsyncClient is injected so it can be shared across requests
    instead of being created anew for each call.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        client: httpx.AsyncClient,
        *,
        view_type: str,
    ) -> None:
        """Store connection settings and the shared HTTP client.

        `view_type` names which of the provider's views this client reads. The
        provider serves one view per call -- `full_view`, `mensapass`, whatever
        a deployment configured -- and refuses a call that names none. It is
        keyword-only because it is the one argument a caller cannot guess from
        the others, and a positional slot is where such an argument gets filled
        with the wrong string.

        One view per client today. The design of 2026-09-05 puts the view on the
        template and the connection on the tenant; until that lands, this is the
        deployment-wide default, and it is what the template's value will
        override.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._client = client
        self._view_type = view_type

    async def fetch_fields(
        self, person_uid: str, fields: list[str], *, view_type: str | None = None
    ) -> dict[str, Any]:
        """Return exactly the requested fields for one person.

        `view_type` names the provider view for this call; None means the
        client's own default, the deployment-wide one. A template that names
        its view passes it here -- the canteen pass reads `mensapass` while
        the student card reads `full_view`, from the same provider.

        Retries once on a connection error. Never retries on an error
        response. Raises ProblemError(502, "data_provider_unavailable")
        without leaking the person UID or any field values.
        """
        payload = json.dumps(
            {
                "person_uid": person_uid,
                "view_type": view_type or self._view_type,
                "fields": fields,
            },
            separators=(",", ":"),
        ).encode()
        for attempt in (1, 2):
            try:
                response = await self._client.post(
                    f"{self._base_url}/lookup",
                    content=payload,
                    headers={**self._headers, "content-type": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.ConnectError:
                if attempt == 2:
                    raise ProblemError(
                        502,
                        "data_provider_unavailable",
                        "Data provider unavailable",
                    ) from None
                continue
            _raise_for_status(response)
            return response.json()
        # Guaranteed to never reach here: each iteration either returns, continues
        # to the next attempt, or raises. On attempt=2, a ConnectError raises
        # without continuing, so we always exit. Required for type completeness.
        raise ProblemError(
            502,
            "data_provider_unavailable",
            "Data provider unavailable",
        )  # pragma: no cover

    async def fetch_catalogue(self) -> list[CatalogueField]:
        """Return the field catalogue offered by the data provider.

        Raises ProblemError(502, "data_provider_unavailable") on connection
        error or non-2xx response without leaking internal details.
        """
        try:
            response = await self._client.get(
                f"{self._base_url}/catalogue",
                params={"view_type": self._view_type},
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.ConnectError:
            raise ProblemError(
                502,
                "data_provider_unavailable",
                "Data provider unavailable",
            ) from None
        _raise_for_status(response)
        return [CatalogueField(**row) for row in response.json()]


def _raise_for_status(response: httpx.Response) -> None:
    """Turn an error response into a problem that says which kind it was.

    A 4xx means THIS service asked wrongly -- a view the provider does not
    know, a field it does not offer, a token it does not accept. A 5xx means
    the provider itself is in trouble. They used to be one problem,
    `data_provider_unavailable`, and that cost a day on 2026-09-05: the
    interface said "no connection" about three healthy replicas answering
    422, and the person reading it went looking for an outage.

    The provider's own title survives into the detail, and so does the status.
    Nothing else does: a person UID or a field value has no business in an
    error a browser may display.
    """
    if response.status_code < 400:
        return
    if response.status_code >= 500:
        raise ProblemError(
            502, "data_provider_unavailable", "Data provider unavailable"
        )
    title = ""
    try:
        body = response.json()
        title = str(body.get("title") or body.get("detail") or "")
    except (ValueError, AttributeError):
        pass
    # RFC 9457: the title is short and stable, the detail carries this
    # occurrence. Putting the status into the title would make every 404 and
    # every 422 a different problem type -- and a client keying on the type
    # would see a new one per failure.
    raise ProblemError(
        502,
        "data_provider_rejected",
        "Data provider rejected the request",
        detail=f"{response.status_code}: {title[:200]}"
        if title
        else str(response.status_code),
    )
