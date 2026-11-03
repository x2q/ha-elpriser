"""Shared fixtures. The JSON files in tests/fixtures are recorded from the live
API (curl -A HA-test https://elpriser.org/api/...), see tests/fixtures/README."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Let Home Assistant load custom_components/elpriser in every test."""
    yield


@pytest.fixture(autouse=True)
async def copenhagen_time_zone(hass) -> None:
    await hass.config.async_set_time_zone("Europe/Copenhagen")
