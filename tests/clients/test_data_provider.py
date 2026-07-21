import httpx
import pytest
import respx

from edutap.pass_builder.clients.data_provider import DataProviderClient
from edutap.pass_builder.errors import ProblemError


def make_client(http: httpx.AsyncClient) -> DataProviderClient:
    return DataProviderClient("http://dp", "", 5.0, http)


@respx.mock
async def test_fetch_fields_sends_projection_and_returns_map():
    route = respx.post("http://dp/lookup").mock(
        return_value=httpx.Response(200, json={"person.name": "Ada"})
    )
    async with httpx.AsyncClient() as http:
        result = await make_client(http).fetch_fields("u1", ["person.name"])
    assert result == {"person.name": "Ada"}
    expected_body = b'{"person_uid":"u1","fields":["person.name"]}'
    assert route.calls.last.request.content == expected_body


@respx.mock
async def test_connection_error_becomes_502_problem():
    respx.post("http://dp/lookup").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProblemError) as excinfo:
            await make_client(http).fetch_fields("u1", ["person.name"])
    assert excinfo.value.slug == "data_provider_unavailable"
