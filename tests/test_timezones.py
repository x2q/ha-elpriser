"""ZoneInfo(...) reads the tz database from disk: never on the event loop (HACS review, 2026-11-03)."""

from __future__ import annotations

import re
from pathlib import Path

from custom_components.elpriser.const import PUBLISH_TIME_TZ, TIME_ZONES, ZONES, time_zone

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "elpriser"


def test_every_zone_has_a_prebuilt_time_zone() -> None:
    assert {zone.tz for zone in ZONES.values()} | {PUBLISH_TIME_TZ} == set(TIME_ZONES)
    for zone in ZONES.values():
        assert time_zone(zone.tz) is TIME_ZONES[zone.tz]
        assert time_zone(zone.tz).key == zone.tz


def test_nothing_builds_a_zoneinfo_at_run_time() -> None:
    """Only const.py (imported in the executor) may call ZoneInfo(...); everything else looks the zone up."""
    offenders = [
        path.name
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name != "const.py" and re.search(r"\bZoneInfo\(", path.read_text())
    ]
    assert offenders == []
