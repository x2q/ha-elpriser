"""The Elpriser integration - electricity prices from elpriser.org."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .api import ElpriserClient, Request
from .const import CONF_GLN, CONF_MODE, CONF_ZONE, DOMAIN, MODE_INKL_ALT, ZONES
from .coordinator import ElpriserConfigEntry, ElpriserCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def build_request(entry: ElpriserConfigEntry) -> Request:
    """The API request a config entry stands for."""
    zone = ZONES[entry.data[CONF_ZONE]]
    if zone.area is None:
        return Request(zone=zone.key)
    return Request(
        zone=zone.key,
        mode=entry.data.get(CONF_MODE, MODE_INKL_ALT),
        gln=entry.data.get(CONF_GLN) or None,
    )


async def async_user_agent(hass: HomeAssistant) -> str:
    """``HomeAssistant-elpriser/<version>`` - tells the service who is asking."""
    integration = await async_get_integration(hass, DOMAIN)
    return f"HomeAssistant-{DOMAIN}/{integration.version}"


async def async_setup_entry(hass: HomeAssistant, entry: ElpriserConfigEntry) -> bool:
    """Set up one zone: a single coordinator, then the entities."""
    client = ElpriserClient(async_get_clientsession(hass), await async_user_agent(hass))
    coordinator = ElpriserCoordinator(hass, entry, client, build_request(entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    coordinator.async_start_timers()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ElpriserConfigEntry) -> None:
    """Options only change what is derived from the data: no new request."""
    entry.runtime_data.async_update_listeners()


async def async_unload_entry(hass: HomeAssistant, entry: ElpriserConfigEntry) -> bool:
    """Unload the platforms (timers are cancelled via the entry)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
