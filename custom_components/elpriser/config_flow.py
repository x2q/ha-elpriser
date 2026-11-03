"""Config flow: pick a zone (Denmark: price type and grid company), then options."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import async_user_agent
from .api import (
    ElpriserClient,
    ElpriserConnectionError,
    ElpriserError,
    ElpriserNoDataError,
    ElpriserRateLimitedError,
    Request,
    parse,
)
from .const import (
    CONF_CHEAP_HOURS,
    CONF_GLN,
    CONF_MODE,
    CONF_SHOW_FORECAST,
    CONF_ZONE,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_SHOW_FORECAST,
    DK_GRID_COMPANIES,
    DK_MODES,
    DOMAIN,
    MAX_CHEAP_HOURS,
    MIN_CHEAP_HOURS,
    MODE_INKL_ALT,
    NET_MODES,
    ZONES,
    grid_company_name,
)
from .prices import PriceDataError

_LOGGER = logging.getLogger(__name__)

_GLN = re.compile(r"\d{13}")


def entry_unique_id(zone: str, mode: str | None, gln: str | None) -> str:
    """One entry per zone - for Denmark per zone + price type + grid company."""
    if ZONES[zone].area is None:
        return zone
    return f"{zone}_{mode or MODE_INKL_ALT}_{gln or 'none'}"


def entry_title(zone: str, mode: str | None, gln: str | None) -> str:
    """Short, unique-ish name used for the device (and so the entity ids)."""
    info = ZONES[zone]
    if info.area is None:
        return f"Elpriser {zone.upper()}"
    title = f"Elpriser {info.area}"
    if gln:
        return f"{title} {grid_company_name(gln) or gln}"
    if mode and mode != MODE_INKL_ALT:
        return f"{title} {mode}"
    return title


class ElpriserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one zone per config entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._zone: str | None = None

    async def _async_check(self, request: Request) -> str | None:
        """Ask the service once; return an error key or None."""
        client = ElpriserClient(async_get_clientsession(self.hass), await async_user_agent(self.hass))
        try:
            response = await client.async_fetch(request)
            if response is not None:
                parse(request, response.payload)
        except ElpriserNoDataError:
            return "no_data"
        except (ElpriserConnectionError, ElpriserRateLimitedError):
            return "cannot_connect"
        except (ElpriserError, PriceDataError):
            _LOGGER.exception("Unexpected answer from elpriser.org")
            return "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            zone = user_input[CONF_ZONE]
            if ZONES[zone].area is not None:
                self._zone = zone
                return await self.async_step_denmark()
            await self.async_set_unique_id(entry_unique_id(zone, None, None))
            self._abort_if_unique_id_configured()
            if (error := await self._async_check(Request(zone=zone))) is None:
                return self.async_create_entry(title=entry_title(zone, None, None), data={CONF_ZONE: zone})
            errors["base"] = error

        options = [
            SelectOptionDict(value=z.key, label=z.label)
            for z in sorted(ZONES.values(), key=lambda z: z.label)
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_denmark(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Danish zones: which price, and optionally which grid company."""
        assert self._zone is not None  # noqa: S101
        area = ZONES[self._zone].area
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input.get(CONF_MODE, MODE_INKL_ALT)
            gln = (user_input.get(CONF_GLN) or "").strip() or None
            if gln is not None and not _GLN.fullmatch(gln):
                errors[CONF_GLN] = "gln_invalid"
            elif mode in NET_MODES and gln is None:
                errors[CONF_GLN] = "gln_required"
            elif mode not in NET_MODES and gln is not None:
                errors[CONF_MODE] = "gln_needs_net_mode"
            else:
                await self.async_set_unique_id(entry_unique_id(self._zone, mode, gln))
                self._abort_if_unique_id_configured()
                if (error := await self._async_check(Request(self._zone, mode, gln))) is None:
                    data: dict[str, Any] = {CONF_ZONE: self._zone, CONF_MODE: mode}
                    if gln:
                        data[CONF_GLN] = gln
                    return self.async_create_entry(title=entry_title(self._zone, mode, gln), data=data)
                errors["base"] = error

        companies = [SelectOptionDict(value=gln, label=name) for name, gln in DK_GRID_COMPANIES[area]]
        schema = vol.Schema(
            {
                vol.Required(CONF_MODE, default=MODE_INKL_ALT): SelectSelector(
                    SelectSelectorConfig(
                        options=list(DK_MODES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="dk_mode",
                    )
                ),
                vol.Optional(CONF_GLN): SelectSelector(
                    SelectSelectorConfig(
                        options=companies,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="denmark",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            description_placeholders={"area": area},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ElpriserOptionsFlow:
        return ElpriserOptionsFlow()


class ElpriserOptionsFlow(OptionsFlow):
    """Number of cheap hours and whether to publish the forecast attribute."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_CHEAP_HOURS: int(user_input[CONF_CHEAP_HOURS]),
                    CONF_SHOW_FORECAST: user_input[CONF_SHOW_FORECAST],
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CHEAP_HOURS,
                    default=options.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_CHEAP_HOURS,
                        max=MAX_CHEAP_HOURS,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SHOW_FORECAST,
                    default=options.get(CONF_SHOW_FORECAST, DEFAULT_SHOW_FORECAST),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
