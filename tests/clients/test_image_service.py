import httpx
import pytest
import respx

from edutap.pass_builder.clients.image_service import ImageServiceClient
from edutap.pass_builder.errors import ProblemError

BASE = "http://image_service:8000"
PHOTO = f"{BASE}/persons/u1/photo/current"


def make_client(http: httpx.AsyncClient) -> ImageServiceClient:
    return ImageServiceClient(BASE, "t0ken", 5.0, http)


@respx.mock
async def test_fetch_returns_the_bytes_and_presents_the_token():
    route = respx.get(PHOTO).mock(return_value=httpx.Response(200, content=b"\x89PNG"))
    async with httpx.AsyncClient() as http:
        assert await make_client(http).fetch(PHOTO) == b"\x89PNG"
    assert route.calls.last.request.headers["authorization"] == "Bearer t0ken"


@respx.mock
async def test_a_reference_elsewhere_is_refused_without_being_fetched():
    """The reference is data, not configuration.

    It arrives from the data provider. A service that fetches whatever URL it
    is handed reaches hosts its caller cannot, and from outside nothing
    distinguishes that from a missing picture.
    """
    route = respx.get("http://elsewhere.invalid/x").mock(
        return_value=httpx.Response(200, content=b"\x89PNG")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch("http://elsewhere.invalid/x")
    assert excinfo.value.slug == "image_reference_rejected"
    assert not route.called


@respx.mock
async def test_a_prefix_that_only_looks_like_the_base_url_is_refused():
    """`http://image_service:8000.evil.invalid/…` must not pass as the base.

    The separator is part of the match, so a host that merely starts with the
    configured one does not satisfy it.
    """
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch(f"{BASE}.evil.invalid/persons/u1")
    assert excinfo.value.slug == "image_reference_rejected"


@respx.mock
async def test_connection_error_becomes_502_problem():
    respx.get(PHOTO).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch(PHOTO)
    assert excinfo.value.slug == "image_service_unavailable"


@respx.mock
async def test_error_response_becomes_502_without_echoing_the_url():
    """The URL carries the person identifier; an error body must not."""
    respx.get(PHOTO).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch(PHOTO)
    assert excinfo.value.slug == "image_service_unavailable"
    assert "u1" not in f"{excinfo.value.title}{excinfo.value.detail or ''}"
