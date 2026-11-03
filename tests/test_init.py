"""Integration set-up: entities, states, attributes, hour ticks, request discipline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.const import CONF_CHEAP_HOURS, CONF_SHOW_FORECAST

from .conftest import load
from .helpers import make_entry, mock_denmark, mock_zone, nordic_url

UTC = UTC
NOW_DE = "2026-11-03T09:20:00+00:00"  # 10:20 in Berlin, hour 10


async def setup(hass: HomeAssistant, entry, *, expect_ok: bool = True) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is expect_ok
    await hass.async_block_till_done()


async def test_germany_entities_and_states(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    now = hass.states.get("sensor.elpriser_de_price_now")
    assert now.state == "0.23343"  # 233.43 EUR/MWh at 10:00-11:00 Berlin
    assert now.attributes["unit_of_measurement"] == "EUR/kWh"
    assert now.attributes["device_class"] == "monetary"
    assert "state_class" not in now.attributes
    assert now.attributes["attribution"].startswith("Data from elpriser.org")
    assert "Elexon" not in now.attributes["attribution"]
    assert hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.22691"
    assert hass.states.get("sensor.elpriser_de_price_today_min").state == "0.22207"
    assert hass.states.get("sensor.elpriser_de_price_today_max").state == "0.31358"
    avg = sum(load("nordic_de")["days"][2]["prices"][h]["eur_mwh"] for h in range(24)) / 24 / 1000
    assert float(hass.states.get("sensor.elpriser_de_price_today_avg").state) == pytest.approx(avg, abs=1e-5)
    cheapest = hass.states.get("sensor.elpriser_de_cheapest_hour_start")
    assert dt_util.parse_datetime(cheapest.state) == datetime(2026, 11, 3, 11, tzinfo=UTC)  # local hour 12
    assert cheapest.attributes["device_class"] == "timestamp"
    level = hass.states.get("sensor.elpriser_de_price_level")
    assert level.state == "cheap" and level.attributes["options"] == ["cheap", "normal", "expensive"]
    assert hass.states.get("binary_sensor.elpriser_de_cheap_hour_now").state == "off"

    # one request for the whole device, with the courteous User-Agent
    assert aioclient_mock.call_count == 1
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["User-Agent"] == "HomeAssistant-elpriser/0.1.0"


async def test_price_now_attributes_follow_the_nordpool_shape(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    attrs = hass.states.get("sensor.elpriser_de_price_now").attributes

    raw_today, raw_tomorrow = attrs["raw_today"], attrs["raw_tomorrow"]
    assert len(raw_today) == 24
    assert set(raw_today[0]) == {"start", "end", "value"}
    assert raw_today[0]["start"] == datetime(2026, 11, 2, 23, tzinfo=UTC)
    assert raw_today[0]["end"] - raw_today[0]["start"] == timedelta(hours=1)
    assert raw_today[0]["value"] == 0.22243
    assert len(raw_tomorrow) == 24  # recorded as a model forecast day: present, but not "valid"
    assert attrs["tomorrow_valid"] is False

    forecast = attrs["forecast"]
    assert forecast[0]["start"] == datetime(2026, 11, 3, 9, tzinfo=UTC)  # starts at the current hour
    assert set(forecast[0]) == {"start", "end", "value", "min", "max", "type"}
    assert forecast[0]["type"] == "actual" and forecast[0]["min"] is None
    types = [f["type"] for f in forecast]
    assert types.count("forecast") == 6 * 24
    assert all(f["min"] <= f["value"] <= f["max"] for f in forecast if f["type"] == "forecast")
    assert "not an official forecast" in attrs["forecast_note"]
    assert attrs["forecast_model"] == "eu-ens-v2"
    assert attrs["price_type"] == "actual" and attrs["zone"] == "de"
    assert attrs["unit"] == "EUR/kWh"


async def test_show_forecast_option_removes_the_forecast_attribute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    assert "forecast" in hass.states.get("sensor.elpriser_de_price_now").attributes
    calls = aioclient_mock.call_count

    hass.config_entries.async_update_entry(entry, options={CONF_SHOW_FORECAST: False})
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.elpriser_de_price_now").attributes
    assert "forecast" not in attrs and "forecast_note" not in attrs
    assert "raw_today" in attrs
    assert aioclient_mock.call_count == calls  # options never cause a request


async def test_cheap_hours_option_changes_the_binary_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T21:20:00+00:00")  # 22:20 Berlin: 230.57, the 12th cheapest hour of the day
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    state = hass.states.get("binary_sensor.elpriser_de_cheap_hour_now")
    assert state.state == "off"
    assert state.attributes["cheap_hours_count"] == 4
    assert [h["start"].astimezone(UTC).hour for h in state.attributes["cheap_hours"]] == [23, 1, 2, 11]
    assert state.attributes["price_threshold"] == 0.22548
    assert state.attributes["next_cheap_start"] is None  # all four are over

    hass.config_entries.async_update_entry(entry, options={CONF_CHEAP_HOURS: 12})
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.elpriser_de_cheap_hour_now")
    assert state.state == "on"
    assert len(state.attributes["cheap_hours"]) == 12
    assert state.attributes["price_threshold"] == 0.23057


async def test_hour_boundary_moves_the_state_without_a_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T09:59:30+00:00")
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.23343"  # hour 10
    assert hass.states.get("binary_sensor.elpriser_de_cheap_hour_now").state == "off"

    freezer.move_to("2026-11-03T10:00:00+00:00")  # 11:00 Berlin
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.22691"  # hour 11
    assert hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.22207"
    assert aioclient_mock.call_count == 1  # the tick re-derives from the data we hold: no request

    freezer.move_to("2026-11-03T10:30:00+00:00")
    async_fire_time_changed(hass)  # nothing is due at :30
    await hass.async_block_till_done()
    freezer.move_to("2026-11-03T11:00:00+00:00")  # 12:00 Berlin: the day's cheapest hour
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.22207"
    assert hass.states.get("binary_sensor.elpriser_de_cheap_hour_now").state == "on"
    assert (
        aioclient_mock.call_count <= 2
    )  # at most the normal hourly refresh (due 10:59:30Z), never one per tick


async def test_midnight_rolls_over_to_the_next_local_day(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T22:30:00+00:00")  # 23:30 Berlin, still Nov 3
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.22842"
    assert (
        hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.19716"
    )  # tomorrow hour 0 (forecast)
    assert hass.states.get("sensor.elpriser_de_price_now").attributes["raw_today"][0]["value"] == 0.22243

    freezer.move_to("2026-11-03T23:00:00+00:00")  # 00:00 Nov 4 in Berlin
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.19716"
    assert hass.states.get("sensor.elpriser_de_price_now").attributes["raw_today"][0]["value"] == 0.19716
    assert hass.states.get("sensor.elpriser_de_price_today_min").state != "0.22207"


async def test_denmark_uses_the_forecast_endpoint_in_dkk(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T03:20:00+00:00")  # 04:20 Copenhagen
    mock_denmark(aioclient_mock)
    await setup(hass, make_entry("dk1", "inkl_alt"))
    now = hass.states.get("sensor.elpriser_dk1_price_now")
    assert now.state == "2.257"  # == /api/now?area=DK1 at hour 4
    assert now.attributes["unit_of_measurement"] == "DKK/kWh"
    assert hass.states.get("sensor.elpriser_dk1_price_next_hour").state == "2.2548"
    assert hass.states.get("sensor.elpriser_dk1_price_today_min").state == "2.209"
    assert (
        hass.states.get("binary_sensor.elpriser_dk1_cheap_hour_now").state == "off"
    )  # /api/schedule: hours 0-3 on
    assert "exchange_rate" not in now.attributes
    assert now.attributes["forecast_model"]["name"] == "v3"
    assert aioclient_mock.call_count == 1
    assert "area=DK1" in str(aioclient_mock.mock_calls[0][1]) and "mode=inkl_alt" in str(
        aioclient_mock.mock_calls[0][1]
    )


async def test_denmark_grid_company_in_the_url_and_title(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T03:20:00+00:00")
    mock_denmark(aioclient_mock, "forecast_dk1_net_inkl_alt", mode="net_inkl_alt", gln="5790000704842")
    await setup(hass, make_entry("dk1", "net_inkl_alt", "5790000704842"))
    assert "gln=5790000704842" in str(aioclient_mock.mock_calls[0][1])
    state = hass.states.get("sensor.elpriser_dk1_konstant_price_now")
    assert state is not None and float(state.state) > 2.257


async def test_non_euro_zone_shows_local_currency_and_rate(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T09:20:00+00:00")
    mock_zone(aioclient_mock, "pl")
    await setup(hass, make_entry("pl"))
    now = hass.states.get("sensor.elpriser_pl_price_now")
    assert now.attributes["unit_of_measurement"] == "PLN/kWh"
    assert now.attributes["exchange_rate"] == 4.4185
    assert now.attributes["exchange_rate_date"] == "2026-11-02"
    expected = load("nordic_pl")["days"][2]["prices"][10]["eur_mwh"] * 4.4185 / 1000
    assert float(now.state) == pytest.approx(expected, abs=1e-5)


async def test_gb_last_complete_hour_licence_and_unknown_cheap_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T03:40:00+00:00")
    mock_zone(aioclient_mock, "gb")
    await setup(hass, make_entry("gb"))
    now = hass.states.get("sensor.elpriser_gb_price_now")
    assert now.state == "0.16703"  # hour 02:00-03:00; 03:00-04:00 is not priced until it is over
    assert now.attributes["unit_of_measurement"] == "GBP/kWh"
    assert now.attributes["price_start"] == datetime(2026, 11, 3, 2, tzinfo=UTC)
    assert now.attributes["attribution"] == (
        "Data from elpriser.org. Contains BMRS data © Elexon Limited copyright and database right 2026"
    )
    assert now.attributes["source_unit"] == "GBP/MWh"
    assert hass.states.get("sensor.elpriser_gb_price_next_hour").state == "unknown"
    assert hass.states.get("binary_sensor.elpriser_gb_cheap_hour_now").state == "unknown"
    assert hass.states.get("sensor.elpriser_gb_price_today_min").state == "0.16703"


async def test_energy_charts_zones_carry_the_cc_by_note(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    attribution = hass.states.get("sensor.elpriser_de_price_now").attributes["attribution"]
    assert "Bundesnetzagentur | SMARD.de" in attribution and "CC BY 4.0" in attribution


async def test_entities_have_unique_ids_and_a_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {e.unique_id for e in entities} == {
        f"de_{k}"
        for k in (
            "price_now",
            "price_next_hour",
            "price_today_min",
            "price_today_max",
            "price_today_avg",
            "cheapest_hour_start",
            "price_level",
            "cheap_hour_now",
        )
    }
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1 and devices[0].manufacturer == "elpriser.org"


async def test_unload_and_reload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.elpriser_de_price_now").state == "unavailable"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.23343"


async def test_setup_retries_when_the_service_is_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(nordic_url("de"), status=503)
    entry = make_entry("de")
    await setup(hass, entry, expect_ok=False)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_independent_entries_make_independent_single_requests(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW_DE)
    mock_zone(aioclient_mock, "de")
    mock_zone(aioclient_mock, "pl")
    await setup(hass, make_entry("de"))
    await setup(hass, make_entry("pl"))
    assert aioclient_mock.call_count == 2  # 8 entities per zone, one request per zone


def _dst_payload(day: str, skip_hour: int | None = None) -> dict:
    return {
        "unit": "EUR/MWh",
        "zoneInfo": {"currency": "EUR", "rate": 1, "rateDate": day},
        "days": [
            {
                "date": day,
                "type": "actual",
                "prices": [{"hour": h, "eur_mwh": 100.0 + h} for h in range(24) if h != skip_hour],
            }
        ],
    }


async def test_25_hour_day_end_to_end(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """2026-10-25: Berlin clocks go back; local hour 2 occurs twice and carries one price."""
    aioclient_mock.get(nordic_url("de"), json=_dst_payload("2026-10-25"))
    freezer.move_to("2026-10-25T00:30:00+00:00")  # 02:30 CEST
    await setup(hass, make_entry("de"))
    state = hass.states.get("sensor.elpriser_de_price_now")
    assert state.state == "0.102"
    assert len(state.attributes["raw_today"]) == 25
    assert hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.102"  # the second 02:00-03:00

    freezer.move_to("2026-10-25T01:00:00+00:00")  # 02:00 CET, the second occurrence
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.102"
    assert hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.103"

    freezer.move_to("2026-10-25T02:00:00+00:00")  # 03:00 CET
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.elpriser_de_price_now").state == "0.103"


async def test_23_hour_day_end_to_end(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """2026-03-29: Berlin clocks go forward; there is no local hour 2."""
    aioclient_mock.get(nordic_url("de"), json=_dst_payload("2026-03-29", skip_hour=2))
    freezer.move_to("2026-03-29T00:30:00+00:00")  # 01:30 CET
    await setup(hass, make_entry("de"))
    state = hass.states.get("sensor.elpriser_de_price_now")
    assert state.state == "0.101"
    assert len(state.attributes["raw_today"]) == 23
    assert hass.states.get("sensor.elpriser_de_price_next_hour").state == "0.103"  # straight to 03:00 CEST
