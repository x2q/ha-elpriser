"""Helpers shared by the Home Assistant tests."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.config_flow import entry_title, entry_unique_id
from custom_components.elpriser.const import (
    CONF_GLN,
    CONF_MODE,
    CONF_ZONE,
    DOMAIN,
)

from .conftest import load

API = "https://elpriser.org/api"


def nordic_url(zone: str) -> str:
    return f"{API}/nordic?zone={zone}"


def forecast_url(area: str, mode: str = "inkl_alt", gln: str | None = None) -> str:
    return f"{API}/forecast?area={area}&mode={mode}" + (f"&gln={gln}" if gln else "")


def mock_zone(mocker: AiohttpClientMocker, zone: str, fixture: str | None = None, **kwargs: Any) -> None:
    mocker.get(nordic_url(zone), json=load(fixture or f"nordic_{zone}"), headers={"ETag": '"v1"'}, **kwargs)


def mock_denmark(
    mocker: AiohttpClientMocker,
    fixture: str = "forecast_dk1_inkl_alt",
    area: str = "DK1",
    mode: str = "inkl_alt",
    gln: str | None = None,
    **kwargs: Any,
) -> None:
    mocker.get(forecast_url(area, mode, gln), json=load(fixture), headers={"ETag": '"v1"'}, **kwargs)


def make_entry(
    zone: str, mode: str | None = None, gln: str | None = None, options: dict | None = None
) -> MockConfigEntry:
    data: dict[str, Any] = {CONF_ZONE: zone}
    if mode:
        data[CONF_MODE] = mode
    if gln:
        data[CONF_GLN] = gln
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options=options or {},
        unique_id=entry_unique_id(zone, mode, gln),
        title=entry_title(zone, mode, gln),
    )
