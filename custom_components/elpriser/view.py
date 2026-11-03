"""A read-only view of PriceData at one moment - what the entities show."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .const import LATEST_KNOWN_MAX_AGE, Zone
from .prices import (
    DayStats,
    PriceData,
    Slot,
    cheapest_slots,
    current_slot,
    day_stats,
    has_day,
    level_position,
    local_date,
    next_slot,
    price_level,
    slot_containing,
    slots_on,
)


class PriceView:
    """Today / tomorrow / now / next for one zone at time `now` (UTC-aware)."""

    def __init__(self, data: PriceData, zone: Zone, now: datetime, cheap_hours: int) -> None:
        self.data = data
        self.zone = zone
        self.now = now
        self.cheap_hours = cheap_hours
        tz = data.zoneinfo
        self.today: date = local_date(now, tz)
        self.tomorrow: date = self.today + timedelta(days=1)
        self.today_slots: list[Slot] = slots_on(data, self.today)
        self.tomorrow_slots: list[Slot] = slots_on(data, self.tomorrow)
        self.stats: DayStats | None = day_stats(self.today_slots)
        self.current: Slot | None = current_slot(
            data, now, latest_known=zone.latest_known, max_age=LATEST_KNOWN_MAX_AGE
        )
        self.next: Slot | None = next_slot(data, now)
        self.cheapest: list[Slot] = cheapest_slots(self.today_slots, cheap_hours)

    @property
    def tomorrow_valid(self) -> bool:
        """Tomorrow's day-ahead prices are published (not just model forecast)."""
        return has_day(self.data, self.tomorrow)

    @property
    def level(self) -> str | None:
        return price_level(self.current.value if self.current else None, self.stats)

    @property
    def level_position(self) -> float | None:
        return level_position(self.current.value if self.current else None, self.stats)

    @property
    def cheapest_today(self) -> Slot | None:
        return self.stats.minimum if self.stats else None

    @property
    def is_cheap_now(self) -> bool | None:
        """Is the hour that contains `now` among the N cheapest of today?

        None when that hour has no price (gb prices an hour only after it ended).
        """
        slot = slot_containing(self.data, self.now)
        if slot is None or slot.value is None:
            return None
        return any(c.start == slot.start for c in self.cheapest)

    @property
    def next_cheap(self) -> Slot | None:
        """First of today's cheap hours that has not ended yet."""
        return next((c for c in self.cheapest if c.end > self.now), None)

    def upcoming(self) -> list[Slot]:
        """Slots from the current hour on (everything the API holds)."""
        return [s for s in self.data.slots if s.end > self.now]
