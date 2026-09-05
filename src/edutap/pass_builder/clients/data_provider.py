"""HTTP client for edutap.data_provider with field projection."""

import json
from typing import Any

import httpx
from pydantic import BaseModel

from ..errors import ProblemError


class CatalogueField(BaseModel):
    """One field the data provider can deliver."""

    key: str
    value_type: str
    label: str | None = None
    required: bool = False
    description: str | None = None


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

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict[str, Any]:
        """Return exactly the requested fields for one person.

        Retries once on a connection error. Never retries on an error
        response. Raises ProblemError(502, "data_provider_unavailable")
        without leaking the person UID or any field values.
        """
        payload = json.dumps(
            {"person_uid": person_uid, "view_type": self._view_type, "fields": fields},
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
