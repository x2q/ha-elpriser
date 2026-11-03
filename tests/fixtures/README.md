Recorded from the live API on 2026-11-03 (UTC 03:42), `curl -A HA-test`:

- `nordic_de.json`, `nordic_pl.json`, `nordic_dk1.json`, `nordic_gb.json` - `GET /api/nordic?zone=<zone>`
- `forecast_dk1_inkl_alt.json`, `forecast_dk2_spot_inkl.json` - `GET /api/forecast?area=DK1&mode=inkl_alt` and `?area=DK2&mode=spot_inkl`
- `forecast_dk1_net_inkl_alt.json` - `GET /api/forecast?area=DK1&mode=net_inkl_alt&gln=5790000704842`
- `prices_dk1.json` - `GET /api/prices?area=DK1&mode=inkl_alt`
- `openapi.json` - `GET /api/openapi.json` (the zone enum is compared with const.ZONES)

At recording time tomorrow (2026-11-04) was not yet published, so it is a `forecast` day;
the `gb` file shows the real shape of the within-day index (null for hours not yet priced).
