"""Thin async client for the elpriser.org API.

Etiquette (it is a free service): one request per refresh for the whole day set,
a descriptive User-Agent, conditional requests (ETag) and a timeout.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import API_BASE, API_TIMEOUT, ZONES, Zone
from .prices import PriceData, PriceDataError, parse_forecast, parse_nordic


class ElpriserError(Exception):
    """Base class for API problems."""


class ElpriserConnectionError(ElpriserError):
    """Network trouble, timeout, or a 5xx from the service."""


class ElpriserNoDataError(ElpriserError):
    """The service has no prices for this request (404/400)."""


class ElpriserRateLimitedError(ElpriserError):
    """HTTP 429; `retry_after` seconds if the server said so."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Request:
    """What to ask for. Hashable and cheap to build from a config entry."""

    zone: str
    mode: str | None = None  # Danish price mode
    gln: str | None = None  # Danish grid company

    @property
    def zone_info(self) -> Zone:
        return ZONES[self.zone]

    @property
    def path_and_params(self) -> tuple[str, dict[str, str]]:
        zone = self.zone_info
        if zone.area is not None:
            params = {"area": zone.area, "mode": self.mode or "inkl_alt"}
            if self.gln:
                params["gln"] = self.gln
            return "/forecast", params
        return "/nordic", {"zone": zone.key}


@dataclass(frozen=True, slots=True)
class Response:
    """Raw payload and the validator to send next time."""

    payload: Any
    etag: str | None


def parse(request: Request, payload: Any) -> PriceData:
    """Turn a raw payload into PriceData (raises PriceDataError)."""
    zone = request.zone_info
    if zone.area is not None:
        return parse_forecast(payload, zone.key, zone.tz)
    return parse_nordic(payload, zone.key, zone.tz)


class ElpriserClient:
    """Fetches the day set of one zone."""

    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        self._session = session
        self._user_agent = user_agent

    async def async_fetch(self, request: Request, etag: str | None = None) -> Response | None:
        """GET the zone's day set. Returns None on 304 Not Modified."""
        path, params = request.path_and_params
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.get(f"{API_BASE}{path}", params=params, headers=headers) as resp:
                    if resp.status == 304:
                        return None
                    if resp.status in (400, 404):
                        raise ElpriserNoDataError(f"HTTP {resp.status} for {request.zone}")
                    if resp.status == 429:
                        raise ElpriserRateLimitedError(_retry_after(resp.headers.get("Retry-After")))
                    if resp.status >= 400:
                        raise ElpriserConnectionError(f"HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
                    return Response(payload=payload, etag=resp.headers.get("ETag"))
        except ElpriserError:
            raise
        except TimeoutError as err:
            raise ElpriserConnectionError("timeout") from err
        except (aiohttp.ClientError, ValueError) as err:  # ValueError: bad JSON
            raise ElpriserConnectionError(str(err)) from err


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


__all__ = [
    "ElpriserClient",
    "ElpriserConnectionError",
    "ElpriserError",
    "ElpriserNoDataError",
    "ElpriserRateLimitedError",
    "PriceDataError",
    "Request",
    "Response",
    "parse",
]
