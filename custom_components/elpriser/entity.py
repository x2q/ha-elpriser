"""Base entity: device info, availability and the per-update PriceView."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTRIBUTION,
    CONF_CHEAP_HOURS,
    DEFAULT_CHEAP_HOURS,
    DOMAIN,
    SOURCE_NOTE_ELEXON,
    SOURCE_NOTE_ENERGY_CHARTS,
    WEBSITE,
)
from .coordinator import ElpriserCoordinator
from .prices import Slot
from .view import PriceView


class ElpriserEntity(CoordinatorEntity[ElpriserCoordinator]):
    """An entity that shows something derived from the zone's price data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElpriserCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        uid = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=entry.title,
            manufacturer="elpriser.org",
            model=coordinator.zone.label,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=WEBSITE,
        )
        self._view: PriceView
        self._refresh_view()

    def _refresh_view(self) -> None:
        """Derive today / now / next from the data we hold, as of this moment."""
        now = dt_util.utcnow()
        cheap_hours = int(self.coordinator.config_entry.options.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS))
        self._view = PriceView(self.coordinator.data, self.coordinator.zone, now, cheap_hours)
        self._attr_attribution = _attribution(self.coordinator.zone.source, now)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_view()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Unavailable if the refresh failed and what we hold no longer covers now."""
        return super().available and self._view.data.covers(self._view.now)

    @property
    def view(self) -> PriceView:
        return self._view


def _attribution(source: str | None, now: datetime) -> str:
    if source == "elexon":
        return f"{ATTRIBUTION}. " + SOURCE_NOTE_ELEXON.format(year=now.year)
    if source == "energy_charts":
        return f"{ATTRIBUTION}. {SOURCE_NOTE_ENERGY_CHARTS}"
    return ATTRIBUTION


def slot_dict(slot: Slot, *, bands: bool = False) -> dict[str, Any]:
    """A slot in the shape the Nordpool-style cards read (HA local datetimes)."""
    out: dict[str, Any] = {
        "start": dt_util.as_local(slot.start),
        "end": dt_util.as_local(slot.end),
        "value": slot.value,
    }
    if bands:
        out["min"] = slot.min
        out["max"] = slot.max
        out["type"] = slot.kind
    return out
