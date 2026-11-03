"""Binary sensor: is the current hour among today's N cheapest?"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ElpriserConfigEntry, ElpriserCoordinator
from .entity import ElpriserEntity, slot_dict

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElpriserConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the binary sensor of one zone."""
    async_add_entities([ElpriserCheapHourNow(entry.runtime_data)])


class ElpriserCheapHourNow(ElpriserEntity, BinarySensorEntity):
    """On while the current hour is one of the N cheapest hours of today.

    "Today" is the zone's own calendar day. N is the "cheap hours" option.
    For Great Britain (a within-day index) an hour is priced only after it is
    over, so the state is unknown there.
    """

    _attr_translation_key = "cheap_hour_now"

    def __init__(self, coordinator: ElpriserCoordinator) -> None:
        super().__init__(coordinator, "cheap_hour_now")

    @property
    def is_on(self) -> bool | None:
        return self._view.is_cheap_now

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self._view
        nxt = view.next_cheap
        return {
            "cheap_hours": [slot_dict(s) for s in view.cheapest],
            "cheap_hours_count": view.cheap_hours,
            "price_threshold": max((s.value for s in view.cheapest), default=None),
            "next_cheap_start": dt_util.as_local(nxt.start) if nxt else None,
            "unit": view.data.unit,
        }
