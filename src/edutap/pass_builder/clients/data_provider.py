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
    ) -> None:
        """Store connection settings and the shared HTTP client."""
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._client = client

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict[str, Any]:
        """Return exactly the requested fields for one person.

        Retries once on a connection error. Never retries on an error
        response. Raises ProblemError(502, "data_provider_unavailable")
        without leaking the person UID or any field values.
        """
        payload = json.dumps(
            {"person_uid": person_uid, "fields": fields},
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
            if response.status_code >= 400:
                raise ProblemError(
                    502,
                    "data_provider_unavailable",
                    "Data provider unavailable",
                )
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
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.ConnectError:
            raise ProblemError(
                502,
                "data_provider_unavailable",
                "Data provider unavailable",
            ) from None
        if response.status_code >= 400:
            raise ProblemError(
                502,
                "data_provider_unavailable",
                "Data provider unavailable",
            )
        return [CatalogueField(**row) for row in response.json()]
