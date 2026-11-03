"""Diagnostics."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.diagnostics import async_get_config_entry_diagnostics

from .helpers import make_entry, mock_zone
from .test_init import setup


async def test_diagnostics(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T09:20:00+00:00")
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"] == {"zone": "de"}
    assert diag["request"] == {"zone": "de", "mode": None, "gln": None}
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["update_interval"] in ("1:00:00", "0:20:00")
    assert diag["data"]["currency"] == "EUR" and diag["data"]["slot_count"] == 216
    assert diag["data"]["model"] == "eu-ens-v2" and diag["data"]["forecast_slots"] == 144
    assert diag["data"]["accuracy"]["version"] == 2
    assert diag["now"]["zone_date"] == "2026-11-03" and diag["now"]["today_slots"] == 24
    assert diag["now"]["current_price"] == 0.23343
    assert "raw_today" not in str(diag)  # no bulk price lists
