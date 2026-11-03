"""Pure parts: parsing, hour/DST handling, currency, cheapest hours, forecast mapping."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.elpriser.const import LATEST_KNOWN_MAX_AGE, ZONES
from custom_components.elpriser.prices import (
    LEVEL_CHEAP,
    LEVEL_EXPENSIVE,
    LEVEL_NORMAL,
    PriceData,
    PriceDataError,
    Slot,
    cheapest_slots,
    current_slot,
    day_stats,
    expand_day,
    has_day,
    local_day_starts,
    next_slot,
    parse_forecast,
    parse_nordic,
    price_level,
    slots_on,
)
from custom_components.elpriser.view import PriceView

from .conftest import load

UTC = UTC
CPH = ZoneInfo("Europe/Copenhagen")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def rows(values: dict[int, float | None]) -> list[dict]:
    return [{"hour": h, "price": v} for h, v in values.items()]


def expand(day: date, tzname: str, values: dict[int, float | None], **kw) -> list[Slot]:
    return expand_day(
        day,
        ZoneInfo(tzname),
        rows(values),
        price_key="price",
        min_key="min",
        max_key="max",
        scale=kw.pop("scale", 1.0),
        kind=kw.pop("kind", "actual"),
    )


# --- the zone table ----------------------------------------------------------


def test_zone_table_matches_live_openapi_enum() -> None:
    enum = load("openapi")["paths"]["/api/nordic"]["get"]["parameters"][0]["schema"]["enum"]
    assert set(ZONES) == set(enum)
    assert len(ZONES) == 40


def test_zone_timezones_are_valid_and_labels_unique() -> None:
    for zone in ZONES.values():
        ZoneInfo(zone.tz)
    assert len({z.label for z in ZONES.values()}) == 40


@pytest.mark.parametrize(
    ("key", "tz"),
    [
        ("dk1", "Europe/Copenhagen"),
        ("fi", "Europe/Copenhagen"),  # the Nordic feed floors to Copenhagen hours, Finland included
        ("nl", "Europe/Copenhagen"),
        ("de", "Europe/Berlin"),
        ("pt", "Europe/Lisbon"),
        ("gb", "Europe/London"),
        ("ie", "Europe/Dublin"),
        ("gr", "Europe/Athens"),
        ("ro", "Europe/Bucharest"),
        ("bg", "Europe/Sofia"),
        ("ee", "Europe/Tallinn"),
        ("lv", "Europe/Riga"),
        ("lt", "Europe/Vilnius"),
    ],
)
def test_zone_wall_clock(key: str, tz: str) -> None:
    assert ZONES[key].tz == tz


def test_only_danish_zones_use_the_forecast_endpoint() -> None:
    assert {z.key for z in ZONES.values() if z.area} == {"dk1", "dk2"}


# --- hours and daylight saving ----------------------------------------------


def test_normal_day_has_24_contiguous_hours() -> None:
    starts = local_day_starts(date(2026, 11, 3), CPH)
    assert len(starts) == 24
    assert starts[0] == utc(2026, 11, 2, 23)
    assert all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(starts))


def test_fall_back_day_has_25_slots_and_the_doubled_hour_repeats_its_value() -> None:
    # 2026-10-25: clocks go back 03:00 CEST -> 02:00 CET. The API has 24 rows; local hour 2 is the mean of both.
    values = {h: float(h) for h in range(24)}
    slots = expand(date(2026, 10, 25), "Europe/Copenhagen", values)
    assert len(slots) == 25
    assert [s.value for s in slots[:5]] == [0.0, 1.0, 2.0, 2.0, 3.0]
    assert slots[2].start == utc(2026, 10, 25, 0)  # 02:00 CEST
    assert slots[3].start == utc(2026, 10, 25, 1)  # 02:00 CET
    assert all(b.start == a.end for a, b in itertools.pairwise(slots))  # no gap, no overlap in real time
    assert slots[-1].end == utc(2026, 10, 25, 23)  # next local midnight (CET)


def test_spring_forward_day_has_23_slots_and_no_hour_2() -> None:
    values = {h: float(h) for h in range(24) if h != 2}  # the API omits the hour that does not exist
    slots = expand(date(2026, 3, 29), "Europe/Copenhagen", values)
    assert len(slots) == 23
    assert [s.value for s in slots[:4]] == [0.0, 1.0, 3.0, 4.0]
    assert slots[2].start == utc(2026, 3, 29, 1)  # 03:00 CEST
    assert all(b.start == a.end for a, b in itertools.pairwise(slots))


def test_spring_forward_with_a_stray_hour_2_row_ignores_it() -> None:
    slots = expand(date(2026, 3, 29), "Europe/Copenhagen", {h: 1.0 for h in range(24)})
    assert len(slots) == 23


@pytest.mark.parametrize(
    ("tzname", "midnight_utc"),
    [
        ("Europe/Copenhagen", utc(2026, 11, 2, 23)),
        ("Europe/Lisbon", utc(2026, 11, 3, 0)),
        ("Europe/London", utc(2026, 11, 3, 0)),
        ("Europe/Athens", utc(2026, 11, 2, 22)),
        ("Europe/Helsinki", utc(2026, 11, 2, 22)),
        ("Europe/Bucharest", utc(2026, 11, 2, 22)),
    ],
)
def test_hour_zero_is_local_midnight_in_each_zone(tzname: str, midnight_utc: datetime) -> None:
    slots = expand(date(2026, 11, 3), tzname, {h: 1.0 for h in range(24)})
    assert slots[0].start == midnight_utc


def test_summer_hour_zero_in_lisbon_vs_copenhagen() -> None:
    lisbon = expand(date(2026, 7, 1), "Europe/Lisbon", {0: 1.0})[0].start
    cph = expand(date(2026, 7, 1), "Europe/Copenhagen", {0: 1.0})[0].start
    assert lisbon - cph == timedelta(hours=1)  # Lisbon midnight is one hour after Copenhagen midnight


def test_fall_back_in_lisbon_doubles_local_hour_1_not_2() -> None:
    # Portugal goes back at 02:00 WEST -> 01:00 WET: the repeated hour is 01:00.
    slots = expand(date(2026, 10, 25), "Europe/Lisbon", {h: float(h) for h in range(24)})
    assert len(slots) == 25
    assert [s.value for s in slots[:4]] == [0.0, 1.0, 1.0, 2.0]


def test_spring_forward_in_athens_has_no_hour_3() -> None:
    # Greece goes forward at 03:00 EET -> 04:00 EEST; local hour 3 does not exist.
    slots = expand(date(2026, 3, 29), "Europe/Athens", {h: float(h) for h in range(24) if h != 3})
    assert len(slots) == 23
    assert [s.value for s in slots[2:5]] == [2.0, 4.0, 5.0]


def test_null_price_keeps_the_slot() -> None:
    slots = expand(date(2026, 11, 3), "Europe/Copenhagen", {0: 1.5, 1: None})
    assert [s.value for s in slots] == [1.5, None]


def test_bad_rows_are_skipped() -> None:
    slots = expand_day(
        date(2026, 11, 3),
        CPH,
        [
            {"hour": "0", "price": 1},
            {"hour": 5, "price": True},
            {"hour": 6, "price": "x"},
            {"hour": 7, "price": 2},
            "junk",
        ],
        price_key="price",
        min_key="min",
        max_key="max",
        scale=1.0,
        kind="actual",
    )
    assert [(s.start.astimezone(CPH).hour, s.value) for s in slots] == [(5, None), (6, None), (7, 2.0)]


# --- parsing the recorded live responses ------------------------------------


def parse_de() -> PriceData:
    return parse_nordic(load("nordic_de"), "de", ZONES["de"].tz)


def test_parse_de_euro() -> None:
    data = parse_de()
    assert (data.currency, data.unit, data.source_unit, data.rate) == ("EUR", "EUR/kWh", "EUR/MWh", 1.0)
    first = data.slots[0]
    assert first.start == utc(2026, 10, 31, 23)  # 2026-11-01 00:00 Berlin (CET)
    assert first.value == pytest.approx(0.24578)  # 245.78 EUR/MWh
    assert first.kind == "actual"
    assert len(data.slots) == 9 * 24
    assert data.model == "eu-ens-v2"
    assert data.forecast_generated == "2026-11-03"
    assert data.accuracy and "byHorizon" in data.accuracy
    assert has_day(data, date(2026, 11, 3))
    assert not has_day(data, date(2026, 11, 4))  # tomorrow is still a model forecast in the recording


def test_parse_de_forecast_days_carry_bands_and_type() -> None:
    data = parse_de()
    tomorrow = slots_on(data, date(2026, 11, 4))
    assert len(tomorrow) == 24
    assert {s.kind for s in tomorrow} == {"forecast"}
    first = tomorrow[0]
    assert first.value == pytest.approx(0.19716)
    assert first.min == pytest.approx(0.16145)
    assert first.max == pytest.approx(0.27616)
    assert first.min <= first.value <= first.max
    # actual days carry no band
    assert slots_on(data, date(2026, 11, 3))[0].min is None


def test_parse_pl_converts_with_zoneinfo_rate() -> None:
    data = parse_nordic(load("nordic_pl"), "pl", ZONES["pl"].tz)
    assert (data.currency, data.unit, data.rate) == ("PLN", "PLN/kWh", 4.4185)
    assert data.slots[0].value == pytest.approx(120.53 * 4.4185 / 1000, abs=1e-5)


def test_parse_dk1_via_nordic_is_dkk_ex_vat() -> None:
    data = parse_nordic(load("nordic_dk1"), "dk1", ZONES["dk1"].tz)
    assert data.currency == "DKK"
    slot = slots_on(data, date(2026, 11, 3))[0]
    assert slot.value == pytest.approx(220 * 7.4737 / 1000, abs=1e-5)


def test_parse_gb_is_gbp_and_unpriced_hours_are_none() -> None:
    data = parse_nordic(load("nordic_gb"), "gb", ZONES["gb"].tz)
    assert (data.currency, data.unit, data.source_unit, data.rate) == ("GBP", "GBP/kWh", "GBP/MWh", 1.0)
    assert data.model is None
    yesterday = slots_on(data, date(2026, 11, 2))
    assert yesterday[0].value == pytest.approx(0.1911)
    today = slots_on(data, date(2026, 11, 3))
    assert [s.value is None for s in today[:4]] == [False, False, False, True]
    assert all(s.kind == "actual" for s in data.slots)
    assert not has_day(data, date(2026, 11, 4))


def test_parse_danish_forecast_is_dkk_per_kwh_unchanged() -> None:
    data = parse_forecast(load("forecast_dk1_inkl_alt"), "dk1", ZONES["dk1"].tz)
    assert (data.currency, data.unit, data.source_unit) == ("DKK", "DKK/kWh", "DKK/kWh")
    assert len(data.slots) == 10 * 24
    today = slots_on(data, date(2026, 11, 3))
    assert today[0].value == 2.209  # == /api/now at hour 0, no conversion
    assert today[0].start == utc(2026, 11, 2, 23)
    assert {s.kind for s in today} == {"actual"}
    tomorrow = slots_on(data, date(2026, 11, 4))
    assert {s.kind for s in tomorrow} == {"forecast"}
    assert tomorrow[0].value == 1.8712
    assert (tomorrow[0].min, tomorrow[0].max) == (1.3459, 2.3308)
    assert data.model["name"] == "v3"
    assert data.forecast_generated == "2026-11-02T12:13:57.958767+00:00"


def test_danish_heuristic_days_are_forecast_days_too() -> None:
    data = parse_forecast(load("forecast_dk1_inkl_alt"), "dk1", ZONES["dk1"].tz)
    last = slots_on(data, date(2026, 11, 12))
    assert {s.kind for s in last} == {"forecast"}


def test_danish_net_mode_matches_the_published_price_on_a_published_day() -> None:
    data = parse_forecast(load("forecast_dk1_net_inkl_alt"), "dk1", ZONES["dk1"].tz)
    prices = load("prices_dk1")  # inkl_alt, no grid tariff
    today = slots_on(data, date(2026, 11, 3))
    # net price = inkl_alt + grid tariff * 1.25 (hour 0: 0.0605 * 1.25)
    assert today[0].value == pytest.approx(prices["prices"][0]["price"] + 0.0605 * 1.25, abs=1e-3)


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"days": []}, {"days": [{"date": "x", "prices": []}]}, {"days": "no"}],
)
def test_malformed_payloads_raise(payload) -> None:
    with pytest.raises(PriceDataError):
        parse_forecast(payload, "dk1", "Europe/Copenhagen")
    with pytest.raises(PriceDataError):
        parse_nordic(payload, "de", "Europe/Berlin")


def test_nordic_payload_without_usable_rate_or_currency_raises() -> None:
    base = load("nordic_de")
    for info in (
        {},
        {"currency": "EUR", "rate": 0},
        {"currency": "EUR"},
        {"rate": 1},
        {"currency": "EURO", "rate": 1},
    ):
        with pytest.raises(PriceDataError):
            parse_nordic({**base, "zoneInfo": info}, "de", "Europe/Berlin")
    with pytest.raises(PriceDataError):
        parse_nordic({k: v for k, v in base.items() if k != "zoneInfo"}, "de", "Europe/Berlin")


def test_overlapping_days_later_entry_wins() -> None:
    payload = {
        "unit": "EUR/MWh",
        "zoneInfo": {"currency": "EUR", "rate": 1},
        "days": [
            {"date": "2026-11-03", "type": "forecast", "prices": [{"hour": 0, "eur_mwh": 100}]},
            {"date": "2026-11-03", "type": "actual", "prices": [{"hour": 0, "eur_mwh": 200}]},
        ],
    }
    data = parse_nordic(payload, "de", "Europe/Berlin")
    assert len(data.slots) == 1
    assert (data.slots[0].value, data.slots[0].kind) == (0.2, "actual")


def test_negative_prices_survive() -> None:
    payload = {
        "unit": "EUR/MWh",
        "zoneInfo": {"currency": "EUR", "rate": 1},
        "days": [{"date": "2026-11-01", "type": "actual", "prices": [{"hour": 10, "eur_mwh": -0.27}]}],
    }
    assert parse_nordic(payload, "pt", "Europe/Lisbon").slots[0].value == -0.00027


# --- now / next / today / cheapest / level ----------------------------------


def day_data(
    values: list[float | None], tzname: str = "Europe/Copenhagen", day: date = date(2026, 11, 3)
) -> PriceData:
    slots = expand(day, tzname, dict(enumerate(values)))
    return PriceData("t", tzname, "EUR", tuple(slots), "EUR/MWh")


def test_current_and_next_slot_switch_exactly_on_the_hour() -> None:
    data = day_data([float(h) for h in range(24)])  # local hour h = h EUR; local midnight is 23:00Z
    assert current_slot(data, utc(2026, 11, 2, 22, 59, 59)) is None  # before the data starts
    assert current_slot(data, utc(2026, 11, 2, 23)).value == 0.0
    assert current_slot(data, utc(2026, 11, 2, 23, 59, 59)).value == 0.0
    assert current_slot(data, utc(2026, 11, 3, 0)).value == 1.0
    assert current_slot(data, utc(2026, 11, 3, 0, 59, 59)).value == 1.0
    assert next_slot(data, utc(2026, 11, 3, 0, 30)).value == 2.0
    assert next_slot(data, utc(2026, 11, 3, 22, 30)) is None  # no hour after local 23
    assert current_slot(data, utc(2026, 11, 3, 22, 30)).value == 23.0
    assert current_slot(data, utc(2026, 11, 3, 23)) is None  # the day is over


def test_current_slot_during_the_doubled_hour() -> None:
    data = PriceData(
        "t",
        "Europe/Copenhagen",
        "EUR",
        tuple(expand(date(2026, 10, 25), "Europe/Copenhagen", {h: float(h) for h in range(24)})),
        "EUR/MWh",
    )
    assert current_slot(data, utc(2026, 10, 25, 0, 30)).value == 2.0  # 02:30 CEST
    assert current_slot(data, utc(2026, 10, 25, 1, 30)).value == 2.0  # 02:30 CET
    assert current_slot(data, utc(2026, 10, 25, 2, 30)).value == 3.0


def test_gb_current_falls_back_to_last_complete_hour_but_not_forever() -> None:
    data = parse_nordic(load("nordic_gb"), "gb", "Europe/London")
    now = utc(2026, 11, 3, 3, 40)  # hour 3 (03:00-04:00) is not priced yet
    assert current_slot(data, now) is None
    last = current_slot(data, now, latest_known=True, max_age=LATEST_KNOWN_MAX_AGE)
    assert last.start == utc(2026, 11, 3, 2)
    assert last.value == pytest.approx(0.16703)
    assert current_slot(data, utc(2026, 11, 3, 7, 0), latest_known=True, max_age=LATEST_KNOWN_MAX_AGE) is None
    assert next_slot(data, now) is None  # next hour is unknown


def test_day_stats_ignore_unpriced_hours() -> None:
    data = parse_nordic(load("nordic_gb"), "gb", "Europe/London")
    stats = day_stats(slots_on(data, date(2026, 11, 3)))
    assert stats.count == 3
    assert stats.minimum.value == pytest.approx(0.16703)
    assert stats.maximum.value == pytest.approx(0.17705)
    assert stats.average == pytest.approx((0.17705 + 0.17023 + 0.16703) / 3, abs=1e-5)
    assert day_stats([]) is None
    assert day_stats(slots_on(data, date(2026, 11, 20))) is None


def test_stats_on_a_25_hour_day_count_the_doubled_hour_twice() -> None:
    slots = expand(date(2026, 10, 25), "Europe/Copenhagen", {h: 10.0 if h == 2 else 0.0 for h in range(24)})
    stats = day_stats(slots)
    assert stats.count == 25
    assert stats.average == pytest.approx(20 / 25)


def test_cheapest_slots_are_the_n_lowest_in_time_order_ties_to_the_earlier() -> None:
    values = [5, 3, 9, 3, 1, 8, 3] + [10] * 17
    slots = expand(date(2026, 11, 3), "Europe/Copenhagen", dict(enumerate(map(float, values))))
    picked = cheapest_slots(slots, 3)
    assert [s.value for s in picked] == [3.0, 3.0, 1.0]
    hours = [s.start.astimezone(CPH).hour for s in picked]
    assert hours == [1, 3, 4]  # the 3.0 at hour 6 loses the tie
    assert cheapest_slots(slots, 0) == []
    assert len(cheapest_slots(slots, 99)) == 24


def test_cheapest_skips_unpriced_hours() -> None:
    slots = expand(date(2026, 11, 3), "Europe/Copenhagen", {0: None, 1: 2.0, 2: 1.0})
    assert [s.value for s in cheapest_slots(slots, 2)] == [2.0, 1.0]


def test_price_level_thirds_and_flat_day() -> None:
    stats = day_stats(expand(date(2026, 11, 3), "Europe/Copenhagen", {0: 0.0, 1: 3.0, 2: 6.0}))
    assert price_level(0.0, stats) == LEVEL_CHEAP
    assert price_level(2.0, stats) == LEVEL_CHEAP  # exactly the lower third
    assert price_level(3.0, stats) == LEVEL_NORMAL
    assert price_level(4.0, stats) == LEVEL_EXPENSIVE  # exactly the upper third
    assert price_level(6.0, stats) == LEVEL_EXPENSIVE
    assert price_level(None, stats) is None and price_level(1.0, None) is None
    flat = day_stats(expand(date(2026, 11, 3), "Europe/Copenhagen", {0: 1.0, 1: 1.0, 2: 1.0}))
    assert price_level(1.0, flat) == LEVEL_NORMAL
    nearly_flat = day_stats(expand(date(2026, 11, 3), "Europe/Copenhagen", {0: 100.0, 1: 100.2}))
    assert price_level(100.0, nearly_flat) == LEVEL_NORMAL


def test_price_level_with_negative_prices() -> None:
    stats = day_stats(expand(date(2026, 11, 3), "Europe/Copenhagen", {0: -1.0, 1: 0.5, 2: 2.0}))
    assert price_level(-1.0, stats) == LEVEL_CHEAP
    assert price_level(0.5, stats) == LEVEL_NORMAL
    assert price_level(2.0, stats) == LEVEL_EXPENSIVE


# --- PriceView ---------------------------------------------------------------


def test_view_for_denmark_at_a_given_moment() -> None:
    data = parse_forecast(load("forecast_dk1_inkl_alt"), "dk1", "Europe/Copenhagen")
    view = PriceView(data, ZONES["dk1"], utc(2026, 11, 3, 3, 30), cheap_hours=4)  # 04:30 CET
    assert view.today == date(2026, 11, 3)
    assert view.current.value == 2.257 and view.next.value == 2.2548  # /api/now at hour 4 says 2.257
    assert view.stats.minimum.value == 2.209
    assert [s.start.astimezone(CPH).hour for s in view.cheapest] == [0, 1, 2, 3]
    assert view.is_cheap_now is False  # hour 4 is the fifth cheapest
    assert view.next_cheap is None  # all four cheap hours (00-03) are over at 04:30
    assert view.tomorrow_valid is False
    assert len(view.tomorrow_slots) == 24
    assert view.level in (LEVEL_CHEAP, LEVEL_NORMAL, LEVEL_EXPENSIVE)
    # matches the service's own /api/schedule answer: hours 0-3 are on
    view = PriceView(data, ZONES["dk1"], utc(2026, 11, 3, 1, 15), cheap_hours=4)  # 02:15
    assert view.is_cheap_now is True


def test_view_uses_the_zones_own_day_not_the_utc_day() -> None:
    data = parse_nordic(load("nordic_de"), "de", "Europe/Berlin")
    # 23:30 UTC on Nov 3 is already Nov 4 in Berlin
    view = PriceView(data, ZONES["de"], utc(2026, 11, 3, 23, 30), cheap_hours=4)
    assert view.today == date(2026, 11, 4)
    assert view.current.kind == "forecast"


def test_view_gb_cheap_now_is_unknown_while_the_hour_is_unpriced() -> None:
    data = parse_nordic(load("nordic_gb"), "gb", "Europe/London")
    view = PriceView(data, ZONES["gb"], utc(2026, 11, 3, 3, 40), cheap_hours=4)
    assert view.is_cheap_now is None
    assert view.current.start == utc(2026, 11, 3, 2)  # last complete hour
    assert view.next is None


def test_slot_count_and_ordering() -> None:
    data = parse_de()
    starts = [s.start for s in data.slots]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert all(
        b.start == a.end for a, b in zip(data.slots, data.slots[1:], strict=False)
    )  # no holes in the recorded feed
