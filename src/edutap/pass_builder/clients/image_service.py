"""HTTP client for edutap.image_service: resolve an image reference to bytes."""

import httpx

from ..errors import ProblemError


class ImageServiceClient:
    """Fetch the bytes an `IMAGE` mapping rule refers to.

    A rule binds a URL, not the picture. The data provider answers JSON, and
    JSON has no bytes -- so a value that has to end up inside a `.pkpass` can
    only travel as a reference and be fetched here.

    The `httpx.AsyncClient` is injected so it can be shared across requests
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

    def _require_known_origin(self, url: str) -> None:
        """Refuse a reference that does not point at the configured service.

        The URL arrives from the data provider, which means it is data and not
        configuration. A service that fetches whatever URL it is handed is a
        request forwarder into its own network: it reaches hosts its caller
        cannot, and the difference between "the image is missing" and "the
        image service answered" is not visible from outside.

        A prefix match against the configured base URL rather than a host
        allow-list, because the base URL is the one address a deployment has
        already had to get right for anything here to work at all.
        """
        if not url.startswith(f"{self._base_url}/"):
            raise ProblemError(
                422,
                "image_reference_rejected",
                "Image reference does not point at the configured image service",
            )

    async def fetch(self, url: str) -> bytes:
        """Return the bytes behind an image reference.

        Retries once on a connection error, never on an error response --
        the same shape as `DataProviderClient.fetch_fields`, and for the same
        reason: a refusal is an answer, and repeating the question does not
        change it.

        Raises `ProblemError(502, "image_service_unavailable")` without
        echoing the URL. It carries the person's identifier, and an error
        body is the one place a privacy-critical path leaks it by accident.
        """
        self._require_known_origin(url)
        for attempt in (1, 2):
            try:
                response = await self._client.get(
                    url, headers=self._headers, timeout=self._timeout
                )
            except httpx.ConnectError:
                if attempt == 2:
                    raise ProblemError(
                        502,
                        "image_service_unavailable",
                        "Image service unavailable",
                    ) from None
                continue
            if response.status_code >= 400:
                raise ProblemError(
                    502,
                    "image_service_unavailable",
                    "Image service unavailable",
                )
            return response.content
        # Unreachable: each iteration returns, continues, or raises; on the
        # second attempt a ConnectError raises rather than continuing.
        raise ProblemError(
            502, "image_service_unavailable", "Image service unavailable"
        )  # pragma: no cover
