"""Manual smoke test against the REAL https://elpriser.org (a handful of requests).

    .venv/bin/python scripts/live_check.py [zone ...]

Runs the same client and parsers the integration uses and prints what the
entities would show right now. Not part of the pytest run (the HA test plugin
blocks sockets by design).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.elpriser.api import ElpriserClient, Request, parse
from custom_components.elpriser.const import ZONES
from custom_components.elpriser.view import PriceView

UA = "HomeAssistant-elpriser/0.1.0 (live check)"
DEFAULT = [
    Request("de"),
    Request("gb"),
    Request("pl"),
    Request("pt"),
    Request("dk1", "inkl_alt"),
    Request("dk2", "net_inkl_alt", "5790000705689"),
]


async def main(requests: list[Request]) -> int:
    failures = 0
    async with aiohttp.ClientSession() as session:
        client = ElpriserClient(session, UA)
        async with session.get("https://elpriser.org/api/openapi.json", headers={"User-Agent": UA}) as resp:
            spec = await resp.json()
        enum = spec["paths"]["/api/nordic"]["get"]["parameters"][0]["schema"]["enum"]
        in_sync = set(enum) == set(ZONES)
        print(f"zone list in sync with the live OpenAPI enum: {in_sync} ({len(enum)} zones)")
        failures += not in_sync
        for req in requests:
            response = await client.async_fetch(req)
            data = parse(req, response.payload)
            view = PriceView(data, ZONES[req.zone], datetime.now(UTC), 4)
            cur, nxt, st = view.current, view.next, view.stats
            print(
                f"{req.zone:4} {req.mode or '':16} {data.unit:8} now={cur.value if cur else None} "
                f"(start {cur.start.isoformat() if cur else None}) next={nxt.value if nxt else None} "
                f"min={st.minimum.value if st else None} max={st.maximum.value if st else None} "
                f"avg={st.average if st else None} level={view.level} cheap_now={view.is_cheap_now} "
                f"cheapest={view.cheapest_today.start.isoformat() if view.cheapest_today else None} "
                f"today={len(view.today_slots)}h tomorrow={len(view.tomorrow_slots)}h "
                f"tomorrow_valid={view.tomorrow_valid} slots={len(data.slots)} "
                f"forecast_slots={sum(s.kind == 'forecast' for s in data.slots)} etag={response.etag}"
            )
            failures += cur is None or not view.today_slots
        # conditional request: same ETag must answer 304 (None)
        first = await client.async_fetch(Request("de"))
        if first.etag:
            again = await client.async_fetch(Request("de"), etag=first.etag)
            print(
                f"conditional GET with ETag {first.etag}: {'304 Not Modified' if again is None else 'full body again'}"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    wanted = [Request(z) if ZONES[z].area is None else Request(z, "inkl_alt") for z in sys.argv[1:]]
    sys.exit(asyncio.run(main(wanted or DEFAULT)))
