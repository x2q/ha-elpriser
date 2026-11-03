"""DataUpdateCoordinator: one gentle request per zone, hourly local ticks."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ElpriserClient,
    ElpriserError,
    ElpriserRateLimitedError,
    Request,
    parse,
)
from .const import (
    BACKOFF_BASE,
    CATCH_UP_END_HOUR,
    CATCH_UP_INTERVAL,
    CATCH_UP_START_HOUR,
    DOMAIN,
    PUBLISH_HOUR,
    PUBLISH_JITTER_SECONDS,
    PUBLISH_MINUTE,
    PUBLISH_TIME_TZ,
    REFRESH_INTERVAL,
    Zone,
)
from .prices import PriceData, PriceDataError, has_day, local_date

_LOGGER = logging.getLogger(__name__)


# --- Pure scheduling helpers (unit tested) -----------------------------------


def next_interval(
    now: datetime,
    data: PriceData | None,
    zone: Zone,
    failures: int,
    retry_after: float | None = None,
) -> timedelta:
    """How long to wait before the next request.

    * errors: 5, 10, 20, 40 minutes (never beyond the normal hour), or the
      server's Retry-After if that is longer;
    * 13:00-18:00 Copenhagen time while tomorrow is still unpublished (and the
      zone has a tomorrow at all): every 20 minutes - the answer is edge cached,
      this only catches the publication;
    * otherwise once an hour.
    """
    if failures > 0:
        backoff = min(BACKOFF_BASE * 2 ** (failures - 1), REFRESH_INTERVAL)
        if retry_after is not None:
            backoff = max(backoff, timedelta(seconds=retry_after))
        return backoff
    if data is not None and not zone.latest_known:
        clock = now.astimezone(ZoneInfo(PUBLISH_TIME_TZ))
        in_window = CATCH_UP_START_HOUR <= clock.hour < CATCH_UP_END_HOUR
        tomorrow = local_date(now, data.zoneinfo) + timedelta(days=1)
        if in_window and not has_day(data, tomorrow):
            return CATCH_UP_INTERVAL
    return REFRESH_INTERVAL


def next_publication(now: datetime, jitter_seconds: float) -> datetime:
    """The next 13:05 Copenhagen time after `now` (UTC-aware) plus jitter."""
    tz = ZoneInfo(PUBLISH_TIME_TZ)
    local = now.astimezone(tz)
    target = local.replace(hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE, second=0, microsecond=0)
    if target <= local:
        # replace() keeps the wall clock, so DST is respected on the next day
        target = (local + timedelta(days=1)).replace(
            hour=PUBLISH_HOUR, minute=PUBLISH_MINUTE, second=0, microsecond=0
        )
    return target.astimezone(dt_util.UTC) + timedelta(seconds=jitter_seconds)


# --- Coordinator -------------------------------------------------------------


class ElpriserCoordinator(DataUpdateCoordinator[PriceData]):
    """Holds the zone's full day set; entities derive "now" from it."""

    config_entry: ElpriserConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ElpriserConfigEntry,
        client: ElpriserClient,
        request: Request,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {request.zone}",
            update_interval=REFRESH_INTERVAL,
        )
        self.client = client
        self.request = request
        self.zone = request.zone_info
        self.failures = 0
        self.last_error: str | None = None
        self._etag: str | None = None
        self._unsub_publish: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> PriceData:
        retry_after: float | None = None
        error: Exception | None = None
        data: PriceData | None = None
        try:
            response = await self.client.async_fetch(
                self.request, etag=self._etag if self.data is not None else None
            )
            if response is None:  # 304: nothing changed since our copy
                data = self.data
            else:
                data = parse(self.request, response.payload)
                self._etag = response.etag
        except ElpriserRateLimitedError as err:
            error, retry_after = err, err.retry_after
        except (ElpriserError, PriceDataError) as err:
            error = err

        now = dt_util.utcnow()
        if error is None and data is not None:
            self.failures = 0
            self.last_error = None
            self.update_interval = next_interval(now, data, self.zone, 0)
            return data

        self.failures += 1
        self.last_error = str(error) if error else "no data"
        self.update_interval = next_interval(now, self.data, self.zone, self.failures, retry_after)
        if self.data is not None and self.data.covers(now):
            # Prices are known days ahead: keep serving what we have rather than
            # flipping every entity to unavailable over a hiccup.
            _LOGGER.warning(
                "Could not refresh %s (%s); using the data we already have, retrying in %s",
                self.request.zone,
                self.last_error,
                self.update_interval,
            )
            return self.data
        raise UpdateFailed(f"Error fetching elpriser.org prices: {self.last_error}") from error

    # --- timers ---------------------------------------------------------------

    @callback
    def async_start_timers(self) -> None:
        """Hour-boundary ticks and the 13:05 CET refresh; cancelled on unload."""
        self.config_entry.async_on_unload(
            async_track_utc_time_change(self.hass, self._async_hour_tick, minute=0, second=0)
        )
        self._schedule_publication()
        self.config_entry.async_on_unload(self._cancel_publication)

    @callback
    def _async_hour_tick(self, _now: datetime) -> None:
        """A new hour: re-derive every entity from the data we hold, no request."""
        self.async_update_listeners()

    @callback
    def _schedule_publication(self) -> None:
        when = next_publication(dt_util.utcnow(), random.uniform(0, PUBLISH_JITTER_SECONDS))  # noqa: S311
        self._unsub_publish = async_track_point_in_utc_time(self.hass, self._async_publication_tick, when)

    @callback
    def _cancel_publication(self) -> None:
        if self._unsub_publish is not None:
            self._unsub_publish()
            self._unsub_publish = None

    async def _async_publication_tick(self, now: datetime) -> None:
        """~13:05 CET: tomorrow's prices should be out - fetch if we lack them."""
        self._schedule_publication()
        if self.data is not None and (
            self.zone.latest_known
            or has_day(self.data, local_date(now, self.data.zoneinfo) + timedelta(days=1))
        ):
            return
        await self.async_request_refresh()


type ElpriserConfigEntry = ConfigEntry[ElpriserCoordinator]
