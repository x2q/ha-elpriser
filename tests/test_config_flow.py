"""Config flow and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.const import (
    CONF_CHEAP_HOURS,
    CONF_GLN,
    CONF_MODE,
    CONF_SHOW_FORECAST,
    CONF_ZONE,
    DOMAIN,
)

from .helpers import API, forecast_url, make_entry, mock_denmark, mock_zone, nordic_url

GLN_RADIUS = "5790000705689"


async def start(hass: HomeAssistant, zone: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONE: zone})


async def test_zone_list_offers_all_40_zones(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    options = result["data_schema"].schema[CONF_ZONE].config["options"]
    assert len(options) == 40
    labels = [o["label"] for o in options]
    assert labels == sorted(labels)
    assert {"value": "gb", "label": "Great Britain (within-day market index, not day-ahead)"} in options


async def test_euro_zone_creates_an_entry_after_one_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_zone(aioclient_mock, "de")
    result = await start(hass, "de")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Elpriser DE"
    assert result["data"] == {CONF_ZONE: "de"}
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2  # the check in the flow, then the entry's first refresh
    assert aioclient_mock.mock_calls[0][3]["User-Agent"].startswith("HomeAssistant-elpriser/")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "de"


async def test_same_zone_twice_aborts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_zone(aioclient_mock, "de")
    make_entry("de").add_to_hass(hass)
    result = await start(hass, "de")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_cannot_connect_and_no_data_errors_then_success(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(nordic_url("de"), exc=TimeoutError)
    result = await start(hass, "de")
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), status=404)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONE: "de"})
    assert result["errors"] == {"base": "no_data"}

    aioclient_mock.clear_requests()
    mock_zone(aioclient_mock, "de")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONE: "de"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unparseable_answer_is_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(nordic_url("de"), json={"days": []})
    result = await start(hass, "de")
    assert result["errors"] == {"base": "unknown"}


async def test_denmark_default_mode_without_grid_company(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_denmark(aioclient_mock)
    result = await start(hass, "dk1")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "denmark"
    assert result["description_placeholders"] == {"area": "DK1"}
    schema = result["data_schema"].schema
    mode_key = next(k for k in schema if k == CONF_MODE)
    assert mode_key.default() == "inkl_alt"
    gln_options = next(v for k, v in schema.items() if k == CONF_GLN).config["options"]
    assert {"value": "5790001089030", "label": "N1"} in gln_options  # DK1 list ...
    assert all(o["label"] != "Radius" for o in gln_options)  # ... not DK2's

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODE: "inkl_alt"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Elpriser DK1"
    assert result["data"] == {CONF_ZONE: "dk1", CONF_MODE: "inkl_alt"}
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == "dk1_inkl_alt_none"


async def test_denmark_with_grid_company(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_denmark(aioclient_mock, "forecast_dk2_spot_inkl", "DK2", "net_inkl_alt", GLN_RADIUS)
    result = await start(hass, "dk2")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE: "net_inkl_alt", CONF_GLN: GLN_RADIUS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Elpriser DK2 Radius"
    assert result["data"] == {CONF_ZONE: "dk2", CONF_MODE: "net_inkl_alt", CONF_GLN: GLN_RADIUS}
    assert f"{API}/forecast?area=DK2&mode=net_inkl_alt&gln={GLN_RADIUS}" in str(
        aioclient_mock.mock_calls[0][1]
    )


async def test_denmark_validation_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    result = await start(hass, "dk1")
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_MODE: "net_inkl_alt"})
    assert result["errors"] == {CONF_GLN: "gln_required"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: "inkl_alt", CONF_GLN: GLN_RADIUS}
    )
    assert result["errors"] == {CONF_MODE: "gln_needs_net_mode"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: "net_inkl_alt", CONF_GLN: "12345"}
    )
    assert result["errors"] == {CONF_GLN: "gln_invalid"}
    assert aioclient_mock.call_count == 0  # nothing is sent for input we can reject locally


async def test_denmark_unknown_gln_is_no_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    gln = "1234567890123"  # a well-formed GLN the service has no tariff for: live answer is 404
    aioclient_mock.get(
        forecast_url("DK1", "net_inkl_alt", gln),
        status=404,
        json={"error": "no nettarif published for gln 1234567890123"},
    )
    result = await start(hass, "dk1")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE: "net_inkl_alt", CONF_GLN: gln}
    )
    assert result["errors"] == {"base": "no_data"}


async def test_denmark_same_zone_different_price_is_a_second_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    make_entry("dk1", "inkl_alt").add_to_hass(hass)
    mock_denmark(aioclient_mock, mode="spot_inkl")
    result = await start(hass, "dk1")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODE: "spot_inkl"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Elpriser DK1 spot_inkl"
    # and the exact same price again aborts
    result = await start(hass, "dk1")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODE: "spot_inkl"})
    assert result["type"] is FlowResultType.ABORT


async def test_options_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    defaults = {k.schema: k.default() for k in result["data_schema"].schema}
    assert defaults == {CONF_CHEAP_HOURS: 4, CONF_SHOW_FORECAST: True}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CHEAP_HOURS: 6.0, CONF_SHOW_FORECAST: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CHEAP_HOURS: 6, CONF_SHOW_FORECAST: False}
    assert isinstance(entry.options[CONF_CHEAP_HOURS], int)
