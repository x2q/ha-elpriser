"""Sensors: current / next hour price, today's range, cheapest hour, price level."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import CONF_SHOW_FORECAST, DEFAULT_SHOW_FORECAST
from .coordinator import ElpriserConfigEntry, ElpriserCoordinator
from .entity import ElpriserEntity, slot_dict
from .prices import PRICE_LEVELS
from .view import PriceView

PARALLEL_UPDATES = 0

FORECAST_NOTE = (
    "Entries with type=forecast are elpriser.org's own statistical model, not an "
    "official forecast, and they are replaced by the settled day-ahead price "
    "(type=actual) when it is published. min/max are the model's P10/P90."
)


@dataclass(frozen=True, kw_only=True)
class ElpriserSensorDescription(SensorEntityDescription):
    """A sensor computed from a PriceView."""

    value_fn: Callable[[PriceView], StateType | datetime]
    attrs_fn: Callable[[PriceView], dict[str, Any]] | None = None
    is_price: bool = False  # unit is "<currency>/kWh" of the zone


def _slot_attrs(slot) -> dict[str, Any]:
    if slot is None:
        return {}
    return {
        "price_start": dt_util.as_local(slot.start),
        "price_end": dt_util.as_local(slot.end),
        "price_type": slot.kind,
    }


def _extreme_attrs(slot) -> dict[str, Any]:
    if slot is None:
        return {}
    return {"start": dt_util.as_local(slot.start), "end": dt_util.as_local(slot.end)}


SENSORS: tuple[ElpriserSensorDescription, ...] = (
    ElpriserSensorDescription(
        key="price_now",
        translation_key="price_now",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=4,
        is_price=True,
        value_fn=lambda v: v.current.value if v.current else None,
        # attrs_fn: handled in ElpriserPriceNowSensor (needs the options)
    ),
    ElpriserSensorDescription(
        key="price_next_hour",
        translation_key="price_next_hour",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=4,
        is_price=True,
        value_fn=lambda v: v.next.value if v.next else None,
        attrs_fn=lambda v: _slot_attrs(v.next),
    ),
    ElpriserSensorDescription(
        key="price_today_min",
        translation_key="price_today_min",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=4,
        is_price=True,
        value_fn=lambda v: v.stats.minimum.value if v.stats else None,
        attrs_fn=lambda v: _extreme_attrs(v.stats.minimum if v.stats else None),
    ),
    ElpriserSensorDescription(
        key="price_today_max",
        translation_key="price_today_max",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=4,
        is_price=True,
        value_fn=lambda v: v.stats.maximum.value if v.stats else None,
        attrs_fn=lambda v: _extreme_attrs(v.stats.maximum if v.stats else None),
    ),
    ElpriserSensorDescription(
        key="price_today_avg",
        translation_key="price_today_avg",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=4,
        is_price=True,
        value_fn=lambda v: v.stats.average if v.stats else None,
        attrs_fn=lambda v: {"hours": v.stats.count} if v.stats else {},
    ),
    ElpriserSensorDescription(
        key="cheapest_hour_start",
        translation_key="cheapest_hour_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda v: v.cheapest_today.start if v.cheapest_today else None,
        attrs_fn=lambda v: (
            {
                "end": dt_util.as_local(v.cheapest_today.end),
                "price": v.cheapest_today.value,
                "unit": v.data.unit,
            }
            if v.cheapest_today
            else {}
        ),
    ),
    ElpriserSensorDescription(
        key="price_level",
        translation_key="price_level",
        device_class=SensorDeviceClass.ENUM,
        options=list(PRICE_LEVELS),
        value_fn=lambda v: v.level,
        attrs_fn=lambda v: {
            "position": v.level_position,
            "today_min": v.stats.minimum.value if v.stats else None,
            "today_max": v.stats.maximum.value if v.stats else None,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElpriserConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors of one zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        (ElpriserPriceNowSensor if d.key == "price_now" else ElpriserSensor)(coordinator, d) for d in SENSORS
    )


class ElpriserSensor(ElpriserEntity, SensorEntity):
    """A value derived from the zone's day set."""

    entity_description: ElpriserSensorDescription

    def __init__(self, coordinator: ElpriserCoordinator, description: ElpriserSensorDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, description.key)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._view.data.unit if self.entity_description.is_price else None

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self._view)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self._view) if fn else None


class ElpriserPriceNowSensor(ElpriserSensor):
    """The headline sensor, with the Nordpool-style price lists as attributes.

    ``raw_today`` / ``raw_tomorrow`` are lists of ``{start, end, value}`` and
    ``forecast`` of ``{start, end, value, min, max, type}``. They are large and
    change only when new data arrives, so they are excluded from the recorder.
    """

    _unrecorded_attributes = frozenset(
        {"raw_today", "raw_tomorrow", "forecast", "forecast_note", "forecast_model"}
    )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self._view
        data = view.data
        attrs: dict[str, Any] = {
            **_slot_attrs(view.current),
            "zone": view.zone.key,
            "zone_name": view.zone.label,
            "currency": data.currency,
            "unit": data.unit,
            "tomorrow_valid": view.tomorrow_valid,
            "raw_today": [slot_dict(s) for s in view.today_slots],
            "raw_tomorrow": [slot_dict(s) for s in view.tomorrow_slots],
        }
        if data.source_unit != data.unit:
            attrs["source_unit"] = data.source_unit
        if data.rate is not None and data.currency != "EUR":
            attrs["exchange_rate"] = data.rate
            attrs["exchange_rate_date"] = data.rate_date
        if self.coordinator.config_entry.options.get(CONF_SHOW_FORECAST, DEFAULT_SHOW_FORECAST):
            attrs["forecast"] = [slot_dict(s, bands=True) for s in view.upcoming()]
            attrs["forecast_note"] = FORECAST_NOTE
            if data.model:
                attrs["forecast_model"] = data.model
        return attrs
