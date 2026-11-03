"""Refresh cadence, back-off, ETag, stale data, the 13:05 CET refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.elpriser.const import (
    CATCH_UP_INTERVAL,
    REFRESH_INTERVAL,
    ZONES,
)
from custom_components.elpriser.coordinator import next_interval, next_publication
from custom_components.elpriser.prices import parse_nordic

from .conftest import load
from .helpers import make_entry, mock_zone, nordic_url
from .test_init import setup

UTC = UTC
DE = ZONES["de"]
GB = ZONES["gb"]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def de_data(tomorrow_published: bool):
    payload = load("nordic_de")
    if tomorrow_published:
        for day in payload["days"]:
            if day["date"] == "2026-11-04":
                day["type"] = "actual"
    return parse_nordic(payload, "de", DE.tz)


# --- pure scheduling ----------------------------------------------------------


@pytest.mark.parametrize(("failures", "minutes"), [(1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (9, 60)])
def test_backoff_doubles_up_to_the_normal_interval(failures: int, minutes: int) -> None:
    assert next_interval(utc(2026, 11, 3, 9), de_data(True), DE, failures) == timedelta(minutes=minutes)


def test_retry_after_is_honoured_when_longer() -> None:
    assert next_interval(utc(2026, 11, 3, 9), None, DE, 1, retry_after=1800) == timedelta(minutes=30)
    assert next_interval(utc(2026, 11, 3, 9), None, DE, 1, retry_after=10) == timedelta(minutes=5)


def test_normal_interval_is_one_hour() -> None:
    assert REFRESH_INTERVAL == timedelta(minutes=60)
    assert next_interval(utc(2026, 11, 3, 9), de_data(False), DE, 0) == REFRESH_INTERVAL  # morning


def test_catch_up_interval_only_while_tomorrow_is_missing_in_the_publication_window() -> None:
    afternoon = utc(2026, 11, 3, 13, 30)  # 14:30 CET
    assert next_interval(afternoon, de_data(False), DE, 0) == CATCH_UP_INTERVAL
    assert next_interval(afternoon, de_data(True), DE, 0) == REFRESH_INTERVAL
    assert (
        next_interval(utc(2026, 11, 3, 18, 30), de_data(False), DE, 0) == REFRESH_INTERVAL
    )  # 19:30 CET: window over
    assert (
        next_interval(utc(2026, 11, 3, 11, 30), de_data(False), DE, 0) == REFRESH_INTERVAL
    )  # 12:30 CET: too early


def test_gb_has_no_tomorrow_so_no_catch_up() -> None:
    data = parse_nordic(load("nordic_gb"), "gb", GB.tz)
    assert next_interval(utc(2026, 11, 3, 13, 30), data, GB, 0) == REFRESH_INTERVAL


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (utc(2026, 11, 3, 3, 0), utc(2026, 11, 3, 12, 5)),  # winter: 13:05 CET = 12:05Z
        (utc(2026, 11, 3, 12, 5), utc(2026, 11, 4, 12, 5)),  # exactly now: next day
        (utc(2026, 11, 3, 20, 0), utc(2026, 11, 4, 12, 5)),
        (utc(2026, 7, 1, 3, 0), utc(2026, 7, 1, 11, 5)),  # summer: 13:05 CEST = 11:05Z
        (utc(2026, 3, 28, 20, 0), utc(2026, 3, 29, 11, 5)),  # the day CEST starts: 13:05 is already CEST
        (utc(2026, 10, 24, 20, 0), utc(2026, 10, 25, 12, 5)),  # the day CEST ends: 13:05 is CET again
    ],
)
def test_next_publication_is_13_05_copenhagen_time(now: datetime, expected: datetime) -> None:
    assert next_publication(now, 0) == expected
    assert next_publication(now, 90) == expected + timedelta(seconds=90)
    assert next_publication(now, 0).astimezone(ZoneInfo("Europe/Copenhagen")).strftime("%H:%M") == "13:05"


# --- the running coordinator --------------------------------------------------


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_refreshes_hourly_with_a_conditional_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T05:00:00+00:00")
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    assert aioclient_mock.call_count == 1
    assert "If-None-Match" not in aioclient_mock.mock_calls[0][3]

    await tick(hass, freezer, timedelta(minutes=30))
    assert aioclient_mock.call_count == 1  # not yet
    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), status=304)
    await tick(hass, freezer, timedelta(minutes=31))
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][3]["If-None-Match"] == '"v1"'
    assert hass.states.get("sensor.elpriser_de_price_now").state != "unavailable"  # 304 kept the data


async def test_failure_keeps_serving_known_prices_and_backs_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T05:00:00+00:00")  # 06:00 Berlin
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    coordinator = entry.runtime_data

    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), status=503)
    await tick(hass, freezer, timedelta(minutes=61))
    assert aioclient_mock.call_count == 1
    assert coordinator.failures == 1 and coordinator.update_interval == timedelta(minutes=5)
    assert "503" in coordinator.last_error
    assert hass.states.get("sensor.elpriser_de_price_now").state != "unavailable"

    await tick(hass, freezer, timedelta(minutes=5, seconds=1))
    assert aioclient_mock.call_count == 2 and coordinator.update_interval == timedelta(minutes=10)
    await tick(hass, freezer, timedelta(minutes=10, seconds=1))
    assert aioclient_mock.call_count == 3 and coordinator.update_interval == timedelta(minutes=20)

    aioclient_mock.clear_requests()
    mock_zone(aioclient_mock, "de")
    await tick(hass, freezer, timedelta(minutes=20, seconds=1))
    assert coordinator.failures == 0 and coordinator.last_error is None
    assert coordinator.update_interval == REFRESH_INTERVAL


async def test_entities_go_unavailable_when_known_prices_run_out(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T05:00:00+00:00")
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), exc=TimeoutError)
    await tick(hass, freezer, timedelta(days=30))  # long after the last forecast hour
    assert hass.states.get("sensor.elpriser_de_price_now").state == "unavailable"
    assert hass.states.get("binary_sensor.elpriser_de_cheap_hour_now").state == "unavailable"


@pytest.mark.parametrize(
    "failure", [{"status": 500}, {"exc": TimeoutError}, {"text": "<html>nope</html>"}, {"json": {"days": []}}]
)
async def test_various_failures_are_survived(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory, failure: dict
) -> None:
    freezer.move_to("2026-11-03T05:00:00+00:00")
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), **failure)
    await tick(hass, freezer, timedelta(minutes=61))
    assert entry.runtime_data.failures == 1
    assert hass.states.get("sensor.elpriser_de_price_now").state != "unavailable"


async def test_rate_limit_retry_after_stretches_the_backoff(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T05:00:00+00:00")
    mock_zone(aioclient_mock, "de")
    entry = make_entry("de")
    await setup(hass, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), status=429, headers={"Retry-After": "1500"})
    await tick(hass, freezer, timedelta(minutes=61))
    assert entry.runtime_data.update_interval == timedelta(minutes=25)


async def test_publication_refresh_fetches_tomorrow_once_with_jitter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T11:30:00+00:00")  # 12:30 CET; tomorrow still a forecast
    mock_zone(aioclient_mock, "de")
    await setup(hass, make_entry("de"))
    assert aioclient_mock.call_count == 1
    assert hass.states.get("sensor.elpriser_de_price_now").attributes["tomorrow_valid"] is False

    # tomorrow gets published; 13:05 CET = 12:05Z, plus up to 5 minutes of jitter
    published = load("nordic_de")
    for day in published["days"]:
        if day["date"] == "2026-11-04":
            day["type"] = "actual"
    aioclient_mock.clear_requests()
    aioclient_mock.get(nordic_url("de"), json=published, headers={"ETag": '"v2"'})
    await tick(hass, freezer, timedelta(minutes=36))  # 12:06Z - the jitter may not have elapsed
    await tick(hass, freezer, timedelta(minutes=5))  # 12:11Z - it has
    assert aioclient_mock.call_count == 1  # exactly one request, not a burst
    assert hass.states.get("sensor.elpriser_de_price_now").attributes["tomorrow_valid"] is True


async def test_publication_tick_costs_nothing_when_tomorrow_is_already_here(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    published = load("nordic_de")
    for day in published["days"]:
        if day["date"] == "2026-11-04":
            day["type"] = "actual"
    freezer.move_to("2026-11-03T11:55:00+00:00")
    aioclient_mock.get(nordic_url("de"), json=published, headers={"ETag": '"v2"'})
    await setup(hass, make_entry("de"))
    assert aioclient_mock.call_count == 1
    # the scheduled hourly refresh is due at 12:55Z; the publication tick (12:05-12:10Z) must not add a request
    await tick(hass, freezer, timedelta(minutes=16))
    assert aioclient_mock.call_count == 1


async def test_gb_never_triggers_a_publication_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03T11:55:00+00:00")
    mock_zone(aioclient_mock, "gb")
    await setup(hass, make_entry("gb"))
    await tick(hass, freezer, timedelta(minutes=16))
    assert aioclient_mock.call_count == 1
