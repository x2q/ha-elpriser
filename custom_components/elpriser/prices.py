"""Pure parsing and price arithmetic - no Home Assistant imports.

Everything here works on plain data so it can be unit tested against recorded
API responses.

The API speaks in *local hours*: ``{"date": "2026-11-03", "prices": [{"hour": 0,
...}]}`` where ``hour`` is the wall-clock hour of the zone (see const.ZONES for
which wall clock). This module turns those into absolute, UTC-anchored one-hour
slots, which is the only representation that survives daylight saving time:

* a 25-hour day (clocks go back): the API carries ONE value for the doubled
  local hour 2 (the mean of both). We emit 25 slots; both 02:00-03:00 slots
  carry that value.
* a 23-hour day (clocks go forward): the API has no hour 2; we emit 23 slots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .const import time_zone

UTC = UTC
HOUR = timedelta(hours=1)
DECIMALS = 5

TYPE_ACTUAL = "actual"
TYPE_FORECAST = "forecast"

LEVEL_CHEAP = "cheap"
LEVEL_NORMAL = "normal"
LEVEL_EXPENSIVE = "expensive"
PRICE_LEVELS = (LEVEL_CHEAP, LEVEL_NORMAL, LEVEL_EXPENSIVE)


class PriceDataError(ValueError):
    """The API answered with something that is not a usable price payload."""


@dataclass(frozen=True, slots=True)
class Slot:
    """One hour of price. start/end are timezone-aware UTC datetimes."""

    start: datetime
    end: datetime
    value: float | None  # local currency per kWh; None = not (yet) known
    min: float | None = None  # P10 on model days
    max: float | None = None  # P90 on model days
    kind: str = TYPE_ACTUAL  # "actual" | "forecast"


@dataclass(frozen=True, slots=True)
class PriceData:
    """A parsed API answer: every priced hour it contains, in time order."""

    zone: str
    tz: str
    currency: str
    slots: tuple[Slot, ...]
    source_unit: str  # what the API said: "EUR/MWh", "GBP/MWh", "DKK/kWh"
    rate: float | None = None  # EUR -> currency, nordic endpoint only
    rate_date: str | None = None
    generated: str | None = None
    model: Any = None  # str (nordic) or dict (Danish forecast), informational
    forecast_generated: str | None = None
    accuracy: Mapping[str, Any] | None = field(default=None, compare=False)

    @property
    def unit(self) -> str:
        """Unit of every value in `slots`."""
        return f"{self.currency}/kWh"

    @property
    def zoneinfo(self) -> ZoneInfo:
        return time_zone(self.tz)

    @property
    def last_end(self) -> datetime | None:
        return self.slots[-1].end if self.slots else None

    def covers(self, now: datetime) -> bool:
        """True if some slot contains `now`."""
        return any(s.start <= now < s.end for s in self.slots)


# --------------------------------------------------------------------------
# Day / hour handling
# --------------------------------------------------------------------------


def local_day_starts(day: date, tz: ZoneInfo) -> list[datetime]:
    """UTC start of every hour of the local calendar day `day` (23, 24 or 25)."""
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    count = round((end - start) / HOUR)
    return [start + i * HOUR for i in range(count)]


def _num(value: Any) -> float | None:
    """A finite number or None. Booleans and strings are not prices."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return float(value)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, DECIMALS)


def expand_day(
    day: date,
    tz: ZoneInfo,
    rows: Iterable[Mapping[str, Any]],
    *,
    price_key: str,
    min_key: str,
    max_key: str,
    scale: float,
    kind: str,
) -> list[Slot]:
    """Turn one API day (rows keyed by local hour) into UTC slots.

    `scale` converts the API number into currency per kWh. A row with a null
    price still yields a slot (value None) so the day keeps its shape.
    """
    by_hour: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        hour = row.get("hour") if isinstance(row, Mapping) else None
        if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23:
            by_hour[hour] = row
    slots: list[Slot] = []
    for start in local_day_starts(day, tz):
        row = by_hour.get(start.astimezone(tz).hour)
        if row is None:
            continue  # the hour that does not exist (spring forward) or a gap
        value = _num(row.get(price_key))
        low = _num(row.get(min_key))
        high = _num(row.get(max_key))
        slots.append(
            Slot(
                start=start,
                end=start + HOUR,
                value=_round(None if value is None else value * scale),
                min=_round(None if low is None or value is None else low * scale),
                max=_round(None if high is None or value is None else high * scale),
                kind=kind,
            )
        )
    return slots


def _parse_days(
    payload: Mapping[str, Any],
    tz: ZoneInfo,
    *,
    price_key: str,
    min_key: str,
    max_key: str,
    scale: float,
) -> tuple[Slot, ...]:
    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise PriceDataError("response has no days")
    by_start: dict[datetime, Slot] = {}
    for day in days:
        if not isinstance(day, Mapping):
            continue
        try:
            the_date = date.fromisoformat(str(day.get("date")))
        except ValueError:
            continue
        rows = day.get("prices")
        if not isinstance(rows, list):
            continue
        kind = TYPE_FORECAST if day.get("type") == TYPE_FORECAST else TYPE_ACTUAL
        for slot in expand_day(
            the_date,
            tz,
            rows,
            price_key=price_key,
            min_key=min_key,
            max_key=max_key,
            scale=scale,
            kind=kind,
        ):
            by_start[slot.start] = slot  # a later day entry wins on overlap
    if not by_start:
        raise PriceDataError("response has no usable hours")
    return tuple(by_start[s] for s in sorted(by_start))


# --------------------------------------------------------------------------
# Parsers for the two endpoints
# --------------------------------------------------------------------------


def parse_nordic(payload: Any, zone: str, tzname: str) -> PriceData:
    """Parse ``/api/nordic?zone=<zone>`` (hourly ``eur_mwh``, zoneInfo with rate).

    Despite the field name ``eur_mwh`` the unit is whatever ``unit`` says
    (``GBP/MWh`` for gb). Local currency per kWh = value * zoneInfo.rate / 1000
    (rate is 1 for gb).
    """
    if not isinstance(payload, Mapping):
        raise PriceDataError("response is not an object")
    info = payload.get("zoneInfo")
    if not isinstance(info, Mapping):
        raise PriceDataError("response has no zoneInfo")
    currency = info.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        raise PriceDataError("zoneInfo has no currency")
    rate = _num(info.get("rate"))
    if rate is None or rate <= 0:
        raise PriceDataError("zoneInfo has no usable rate")
    unit = payload.get("unit")
    source_unit = unit if isinstance(unit, str) else "EUR/MWh"
    slots = _parse_days(
        payload,
        time_zone(tzname),
        price_key="eur_mwh",
        min_key="min_eur_mwh",
        max_key="max_eur_mwh",
        scale=rate / 1000,
    )
    accuracy = payload.get("accuracy")
    return PriceData(
        zone=zone,
        tz=tzname,
        currency=currency.upper(),
        slots=slots,
        source_unit=source_unit,
        rate=rate,
        rate_date=_str(info.get("rateDate")),
        generated=_str(payload.get("generated")),
        model=_str(payload.get("model")),
        forecast_generated=_str(payload.get("forecastGenerated")),
        accuracy=accuracy if isinstance(accuracy, Mapping) else None,
    )


def parse_forecast(payload: Any, zone: str, tzname: str) -> PriceData:
    """Parse the Danish ``/api/forecast`` (``price`` in DKK/kWh, ``min``/``max``)."""
    if not isinstance(payload, Mapping):
        raise PriceDataError("response is not an object")
    slots = _parse_days(
        payload,
        time_zone(tzname),
        price_key="price",
        min_key="min",
        max_key="max",
        scale=1.0,
    )
    model = payload.get("model")
    return PriceData(
        zone=zone,
        tz=tzname,
        currency="DKK",
        slots=slots,
        source_unit="DKK/kWh",
        generated=_str(payload.get("generated")),
        model=dict(model) if isinstance(model, Mapping) else None,
        forecast_generated=(_str(model.get("generatedAt")) if isinstance(model, Mapping) else None),
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------
# Queries on parsed data
# --------------------------------------------------------------------------


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def slots_on(data: PriceData, day: date) -> list[Slot]:
    """The slots of the zone's local calendar day `day`."""
    tz = data.zoneinfo
    return [s for s in data.slots if local_date(s.start, tz) == day]


def slot_containing(data: PriceData, now: datetime) -> Slot | None:
    for slot in data.slots:
        if slot.start <= now < slot.end:
            return slot
    return None


def current_slot(
    data: PriceData, now: datetime, *, latest_known: bool = False, max_age: timedelta = timedelta(hours=3)
) -> Slot | None:
    """The slot with the price that applies right now.

    With `latest_known` (gb: an hour is published only once it is over) a
    missing price falls back to the last priced hour that ended no more than
    `max_age` ago.
    """
    slot = slot_containing(data, now)
    if slot is not None and slot.value is not None:
        return slot
    if not latest_known:
        return None
    past = [s for s in data.slots if s.value is not None and s.end <= now and now - s.end <= max_age]
    return past[-1] if past else None


def next_slot(data: PriceData, now: datetime) -> Slot | None:
    """The slot that starts at the next hour boundary (None if absent/unpriced)."""
    current = slot_containing(data, now)
    if current is None:
        return None
    for slot in data.slots:
        if slot.start == current.end:
            return slot if slot.value is not None else None
    return None


@dataclass(frozen=True, slots=True)
class DayStats:
    minimum: Slot
    maximum: Slot
    average: float
    count: int


def day_stats(slots: Sequence[Slot]) -> DayStats | None:
    """Min / max slot and the mean over the priced slots (None if there are none)."""
    priced = [s for s in slots if s.value is not None]
    if not priced:
        return None
    return DayStats(
        minimum=min(priced, key=lambda s: (s.value, s.start)),
        maximum=max(priced, key=lambda s: (s.value, -s.start.timestamp())),
        average=round(sum(s.value for s in priced) / len(priced), DECIMALS),
        count=len(priced),
    )


def cheapest_slots(slots: Sequence[Slot], count: int) -> list[Slot]:
    """The `count` cheapest priced slots, earliest wins a tie, in time order."""
    priced = [s for s in slots if s.value is not None]
    chosen = sorted(priced, key=lambda s: (s.value, s.start))[: max(count, 0)]
    return sorted(chosen, key=lambda s: s.start)


def price_level(value: float | None, stats: DayStats | None) -> str | None:
    """cheap / normal / expensive relative to today's range.

    The lowest third of the range is cheap, the highest third expensive. A flat
    day (range below 0.5 % of the larger absolute price, or zero) is normal.
    """
    if value is None or stats is None:
        return None
    low, high = stats.minimum.value, stats.maximum.value
    span = high - low
    if span <= 0 or span < 0.005 * max(abs(low), abs(high)):
        return LEVEL_NORMAL
    position = (value - low) / span
    if position <= 1 / 3:
        return LEVEL_CHEAP
    if position >= 2 / 3:
        return LEVEL_EXPENSIVE
    return LEVEL_NORMAL


def level_position(value: float | None, stats: DayStats | None) -> float | None:
    """0.0 (cheapest hour of the day) .. 1.0 (dearest), None if unknown."""
    if value is None or stats is None:
        return None
    span = stats.maximum.value - stats.minimum.value
    if span <= 0:
        return 0.0
    return round((value - stats.minimum.value) / span, 3)


def has_day(data: PriceData, day: date) -> bool:
    """True if `day` has at least one priced `actual` slot (i.e. it is published)."""
    return any(s.value is not None and s.kind == TYPE_ACTUAL for s in slots_on(data, day))
