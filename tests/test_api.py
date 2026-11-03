"""The HTTP client: URLs, headers, status handling."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.api import (
    ElpriserClient,
    ElpriserConnectionError,
    ElpriserNoDataError,
    ElpriserRateLimitedError,
    Request,
)

from .conftest import load
from .helpers import forecast_url, nordic_url

UA = "HomeAssistant-elpriser/9.9.9"


def test_request_urls() -> None:
    assert Request("de").path_and_params == ("/nordic", {"zone": "de"})
    assert Request("it-n").path_and_params == ("/nordic", {"zone": "it-n"})
    assert Request("dk1", "inkl_alt").path_and_params == ("/forecast", {"area": "DK1", "mode": "inkl_alt"})
    assert Request("dk2", "net_inkl_alt", "5790000705689").path_and_params == (
        "/forecast",
        {"area": "DK2", "mode": "net_inkl_alt", "gln": "5790000705689"},
    )
    assert Request("dk1").path_and_params[1]["mode"] == "inkl_alt"


async def test_fetch_sends_user_agent_and_returns_etag(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(nordic_url("de"), json=load("nordic_de"), headers={"ETag": '"abc"'})
    client = ElpriserClient(async_get_clientsession(hass), UA)
    response = await client.async_fetch(Request("de"))
    assert response.etag == '"abc"' and response.payload["zone"] == "de"
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["User-Agent"] == UA and "If-None-Match" not in headers


async def test_fetch_conditional_304(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(forecast_url("DK1"), status=304)
    client = ElpriserClient(async_get_clientsession(hass), UA)
    assert await client.async_fetch(Request("dk1"), etag='"abc"') is None
    assert aioclient_mock.mock_calls[0][3]["If-None-Match"] == '"abc"'


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 404}, ElpriserNoDataError),
        ({"status": 400}, ElpriserNoDataError),
        ({"status": 429, "headers": {"Retry-After": "120"}}, ElpriserRateLimitedError),
        ({"status": 500}, ElpriserConnectionError),
        ({"status": 403}, ElpriserConnectionError),
        ({"exc": TimeoutError}, ElpriserConnectionError),
        ({"text": "not json"}, ElpriserConnectionError),
    ],
)
async def test_fetch_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict, error: type
) -> None:
    aioclient_mock.get(nordic_url("de"), **kwargs)
    client = ElpriserClient(async_get_clientsession(hass), UA)
    with pytest.raises(error) as info:
        await client.async_fetch(Request("de"))
    if error is ElpriserRateLimitedError:
        assert info.value.retry_after == 120.0
