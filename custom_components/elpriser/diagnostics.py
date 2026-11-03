"""Diagnostics: what the integration holds, without bulk price lists."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS
from .coordinator import ElpriserConfigEntry
from .view import PriceView


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ElpriserConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (there are no secrets to redact)."""
    coordinator = entry.runtime_data
    data = coordinator.data
    now = dt_util.utcnow()
    view = PriceView(
        data,
        coordinator.zone,
        now,
        int(entry.options.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS)),
    )
    first, last = (data.slots[0], data.slots[-1]) if data.slots else (None, None)
    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "request": {
            "zone": coordinator.request.zone,
            "mode": coordinator.request.mode,
            "gln": coordinator.request.gln,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "consecutive_failures": coordinator.failures,
            "last_error": coordinator.last_error,
        },
        "data": {
            "zone": data.zone,
            "timezone": data.tz,
            "currency": data.currency,
            "unit": data.unit,
            "source_unit": data.source_unit,
            "exchange_rate": data.rate,
            "exchange_rate_date": data.rate_date,
            "generated": data.generated,
            "model": data.model,
            "forecast_generated": data.forecast_generated,
            "accuracy": data.accuracy,
            "slot_count": len(data.slots),
            "first_slot": first.start.isoformat() if first else None,
            "last_slot_end": last.end.isoformat() if last else None,
            "forecast_slots": sum(1 for s in data.slots if s.kind == "forecast"),
        },
        "now": {
            "utc": now.isoformat(),
            "zone_date": view.today.isoformat(),
            "today_slots": len(view.today_slots),
            "tomorrow_slots": len(view.tomorrow_slots),
            "tomorrow_valid": view.tomorrow_valid,
            "current_price": view.current.value if view.current else None,
            "level": view.level,
            "cheap_now": view.is_cheap_now,
        },
    }
