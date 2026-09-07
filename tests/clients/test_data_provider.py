import httpx
import pytest
import respx

from edutap.pass_builder.clients.data_provider import DataProviderClient
from edutap.pass_builder.errors import ProblemError


def make_client(http: httpx.AsyncClient) -> DataProviderClient:
    return DataProviderClient("http://dp", "", 5.0, http, view_type="full_view")


@respx.mock
async def test_fetch_fields_sends_projection_and_returns_map():
    route = respx.post("http://dp/lookup").mock(
        return_value=httpx.Response(200, json={"person.name": "Ada"})
    )
    async with httpx.AsyncClient() as http:
        result = await make_client(http).fetch_fields("u1", ["person.name"])
    assert result == {"person.name": "Ada"}
    expected_body = (
        b'{"person_uid":"u1","view_type":"full_view","fields":["person.name"]}'
    )
    assert route.calls.last.request.content == expected_body


@respx.mock
async def test_connection_error_becomes_502_problem():
    respx.post("http://dp/lookup").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_fields("u1", ["person.name"])
    assert excinfo.value.slug == "data_provider_unavailable"


@respx.mock
async def test_catalogue_connection_error_becomes_502_problem():
    respx.get("http://dp/catalogue").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_catalogue()
    assert excinfo.value.slug == "data_provider_unavailable"


@respx.mock
async def test_catalogue_asks_for_one_view():
    """The provider serves one view at a time and refuses a call that names none.

    Measured against the deployed `edutap.data_provider` on 2026-09-05: a bare
    `GET /catalogue` answers 422 `view_type: Field required`. This client used to
    send exactly that, and then reported the 422 as the provider being
    unavailable -- the interface said "no connection" about a service that was
    up and answering.
    """
    route = respx.get("http://dp/catalogue").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with httpx.AsyncClient() as http:
        await make_client(http).fetch_catalogue()
    assert route.calls.last.request.url.params["view_type"] == "full_view"


@respx.mock
async def test_a_refused_request_is_not_reported_as_unavailable():
    """A 4xx means this service asked wrongly; a 5xx means the provider is down.

    Folding both into `data_provider_unavailable` cost a day: an operator reads
    "unavailable", goes looking for an outage, and finds three healthy replicas.
    The status and the provider's own message have to survive into the problem.
    """
    respx.get("http://dp/catalogue").mock(
        return_value=httpx.Response(
            404, json={"title": "Unknown view type", "detail": "View type 'nope'"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_catalogue()
    assert excinfo.value.slug == "data_provider_rejected"
    assert excinfo.value.detail == "404: Unknown view type"


@respx.mock
async def test_a_provider_error_is_still_unavailable():
    respx.get("http://dp/catalogue").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_catalogue()
    assert excinfo.value.slug == "data_provider_unavailable"


@respx.mock
async def test_a_view_per_call_overrides_the_clients_default():
    """The client carries the deployment default; a template may name its own."""
    route = respx.post("http://dp/lookup").mock(
        return_value=httpx.Response(200, json={"person.name": "Ada"})
    )
    async with httpx.AsyncClient() as http:
        await make_client(http).fetch_fields(
            "u1", ["person.name"], view_type="mensapass"
        )
    assert b'"view_type":"mensapass"' in route.calls.last.request.content


@respx.mock
async def test_no_view_per_call_means_the_clients_default():
    route = respx.post("http://dp/lookup").mock(
        return_value=httpx.Response(200, json={"person.name": "Ada"})
    )
    async with httpx.AsyncClient() as http:
        await make_client(http).fetch_fields("u1", ["person.name"], view_type=None)
    assert b'"view_type":"full_view"' in route.calls.last.request.content
