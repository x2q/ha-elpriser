# Elpriser for Home Assistant

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=x2q&repository=ha-elpriser&category=integration)
[![Tests](https://github.com/x2q/ha-elpriser/actions/workflows/tests.yml/badge.svg)](https://github.com/x2q/ha-elpriser/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Hourly electricity prices from [elpriser.org](https://elpriser.org) as Home Assistant sensors: the price
right now and next hour, today's lowest / highest / average, the cheapest hour, a cheap / normal / expensive
level and a "this is one of the N cheapest hours" switch for automations.

* **40 price zones**: Denmark (DK1 and DK2, optionally with your grid company's tariff), the other Nordic
  and Dutch zones, and most of continental Europe, Great Britain and Ireland.
* **Free, no account, no API key, no telemetry.** The integration only talks to `elpriser.org`.
* **Gentle on the service**: one request per zone per hour for the whole day set (more below).
* Works with the **Energy dashboard** and with the Nordpool-style `raw_today` / `raw_tomorrow` attributes that
  the popular ApexCharts price cards read.
* English, Danish and German.

<!-- Screenshots: add docs/screenshot-device.png and docs/screenshot-apexcharts.png, then uncomment.
![Device page](docs/screenshot-device.png)
![ApexCharts card](docs/screenshot-apexcharts.png)
-->

## Install

### With HACS (custom repository)

1. In Home Assistant open **HACS**, open the three-dot menu, **Custom repositories**.
2. Add `https://github.com/x2q/ha-elpriser` with category **Integration**.
3. Find **Elpriser** in HACS, **Download**, then **restart Home Assistant**.
4. **Settings → Devices & services → Add integration → Elpriser.**

### Manually

Copy `custom_components/elpriser` into the `custom_components` folder of your Home Assistant configuration
and restart.

## Set-up

Pick a price zone from the list. Add the integration once per zone you care about.

* **Denmark (DK1 / DK2)** asks for a *price type* and, optionally, your *grid company* (netselskab):

  | Price type | What it is |
  | --- | --- |
  | `inkl_alt` (default) | Spot + Energinet's system and transmission tariffs + electricity tax + 25 % VAT. Everything except your own grid company's tariff. |
  | `spot_inkl` / `spot_ex` | The raw spot price with / without VAT. |
  | `inkl_alt_minus` | Like `inkl_alt` but without the electricity tax. |
  | `inkl_alt_elvarme` | Like `inkl_alt` with the reduced tax for electrically heated homes. |
  | `net_inkl_alt` | **The full price you pay**: `inkl_alt` plus your grid company's tariff. Needs a grid company. |
  | `net_inkl_alt_elvarme`, `net_inkl_tarif` | Variants of the above (reduced tax / no tax). Need a grid company. |

  The grid company can be picked from the list or typed as its 13-digit GLN number.
  Tariffs are applied per day, so a tariff change at midnight is picked up on the right day.
* **Every other zone** shows the **wholesale (day-ahead) price without VAT, taxes and grid fees** in the
  zone's local currency (converted with the ECB reference rate, shown as the `exchange_rate` attribute) -
  not what a household pays.

**Options** (the cog on the entry, no extra requests): the number of cheap hours (default 4) and whether to
add the `forecast` attribute (default on).

## Entities

For every zone you get one device with these entities (the ids below are for the default English names,
e.g. `sensor.elpriser_de_price_now`; Danish and German installs get translated names):

| Entity | State |
| --- | --- |
| `sensor.…_price_now` | The price of the current hour, in `<currency>/kWh` (device class *monetary*). Carries the price lists below. |
| `sensor.…_price_next_hour` | The price of the next hour. |
| `sensor.…_price_today_min` / `_max` / `_avg` | Lowest, highest and average hourly price of today (the zone's own calendar day). |
| `sensor.…_cheapest_hour_start` | Timestamp when today's cheapest hour starts. |
| `sensor.…_price_level` | `cheap`, `normal` or `expensive`: the current price within today's range (lowest third / middle / highest third; a flat day is `normal`). |
| `binary_sensor.…_cheap_hour_now` | `on` while the current hour is one of today's N cheapest (N = *cheap hours* option). Its `cheap_hours` attribute lists them. |

Example states from the live service (2026-11-03 04:50 CET):

| Zone | `price_now` | `price_next_hour` | today min / max / avg | `price_level` |
| --- | --- | --- | --- | --- |
| DK1, `inkl_alt` | 2.257 DKK/kWh | 2.2548 | 2.209 / 3.0903 / 2.5224 | cheap |
| DK2, `net_inkl_alt`, Radius | 2.4021 DKK/kWh | 2.4327 | 2.3814 / 4.4043 / 3.1377 | cheap |
| DE | 0.22582 EUR/kWh | 0.22656 | 0.22207 / 0.31358 / 0.25285 | cheap |
| PL | 1.01648 PLN/kWh | 0.99712 | 0.93354 / 1.64956 / 1.30504 | cheap |
| GB | 0.16703 GBP/kWh (last complete hour) | unknown | 0.16703 / 0.17705 / 0.17144 | cheap |

### Attributes of `price_now`

| Attribute | Content |
| --- | --- |
| `raw_today`, `raw_tomorrow` | Lists of `{start, end, value}` for today and tomorrow (empty until published). The shape the Nordpool-style cards read. |
| `forecast` | Lists of `{start, end, value, min, max, type}` from the current hour to the end of the data (up to ten days). `type` is `actual` (a published day-ahead price) or `forecast`. `min`/`max` are the model's P10/P90 band on forecast hours. Turn it off in the options if you do not want it. |
| `tomorrow_valid` | `true` once tomorrow's day-ahead prices are published (not just model forecast). |
| `price_start`, `price_end`, `price_type` | The hour the state belongs to. |
| `unit`, `currency`, `zone`, `exchange_rate`, … | Where the number comes from. |

`raw_today`, `raw_tomorrow` and `forecast` are excluded from the recorder (they are large and carry nothing
that is not already in the history of the state).

> **About `type: forecast`.** Forecast hours come from **elpriser.org's own statistical model - not an
> official forecast.** It ranks the cheapest hours of a day better than it hits the price *level*, the error
> grows with the horizon, and it is replaced by the settled day-ahead price as soon as that is published
> (about 13:00 CET for the next day). Do not use it for anything that needs a guaranteed price. The model and
> its back-tested accuracy are described on [elpriser.org](https://elpriser.org/prognose). No forecast is
> available for `gb` (and `xk`).

### Great Britain

`gb` is not a day-ahead price: it is the within-day **Market Index Price** from Elexon BMRS in GBP, and an
hour is only priced once it is over. So `price_now` shows the **last complete hour** (see `price_start`; for
at most three hours), `price_next_hour` is unknown, there is no tomorrow, and `cheap_hour_now` is unknown.
The sensors carry the attribution *Contains BMRS data © Elexon Limited copyright and database right <year>*.

### Hours, time zones and daylight saving

The service speaks in local hours (0-23). Each zone's hours are read on that zone's own wall clock
(Copenhagen for Denmark, Norway, Sweden, Finland and the Netherlands; Berlin for Germany; Lisbon for
Portugal; Athens for Greece; and so on), and "today" is that zone's calendar day. The integration turns them
into absolute one-hour slots, so on a 25-hour day (clocks go back) the repeated hour carries its one price for
both occurrences and on a 23-hour day the missing hour is simply absent. Timestamps in the attributes are in
your Home Assistant time zone.

## Energy dashboard

*Settings → Dashboards → Energy → Grid consumption → Use an entity with current price* and pick
`sensor.…_price_now`. The sensor's unit is `<currency>/kWh`; the Energy dashboard offers price sensors whose
currency matches your Home Assistant currency (*Settings → System → General*), so use a zone that is priced in
your currency (DKK for Danish zones, EUR for most of the rest, PLN for `pl`, NOK / SEK for Norway / Sweden).

## Examples

Run the dishwasher in a cheap hour:

```yaml
automation:
  - alias: Dishwasher in a cheap hour
    triggers:
      - trigger: state
        entity_id: binary_sensor.elpriser_dk1_cheap_hour_now
        to: "on"
    conditions:
      - condition: state
        entity_id: input_boolean.dishwasher_loaded
        state: "on"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.dishwasher
```

Chart for today and tomorrow with [apexcharts-card](https://github.com/RomRider/apexcharts-card):

```yaml
type: custom:apexcharts-card
graph_span: 48h
span:
  start: day
header:
  show: true
  title: Electricity price
series:
  - entity: sensor.elpriser_dk1_price_now
    type: column
    data_generator: |
      return entity.attributes.raw_today
        .concat(entity.attributes.raw_tomorrow)
        .map((e) => [new Date(e.start).getTime(), e.value]);
```

## API etiquette

elpriser.org is a free service that is edge-cached for 30-60 minutes. The integration is built to stay out of
the way:

* **One request per zone per hour** for the *whole* day set (today, tomorrow once published, the forecast
  days). Never one request per sensor.
* **No polling for "now".** The current hour is picked locally from that data at every hour boundary.
* Conditional requests (`If-None-Match` / `ETag`), so an unchanged answer costs a `304`.
* One extra refresh at about **13:05 CET plus a random 0-5 minutes** - when tomorrow's prices are published -
  and only if tomorrow is not already there. Until tomorrow shows up, a refresh every 20 minutes between 13:00
  and 18:00 CET.
* **Back-off on errors** (5, 10, 20, 40 minutes, then hourly; `Retry-After` is honoured). While the data we
  hold still covers the current hour, the entities keep showing it instead of going unavailable.
* A `User-Agent` of `HomeAssistant-elpriser/<version>`; a 30 second timeout.

No API key, no account, no analytics, nothing is sent except the zone (and for Denmark the price type and the
optional grid company GLN) in the request URL.

## Data sources and licences

Prices are served by elpriser.org from Energi Data Service (Energinet), ENTSO-E, Energy-Charts, SEMOpx and
Elexon. Zones taken from Bundesnetzagentur | SMARD.de via Energy-Charts.info (CC BY 4.0) carry that
attribution, and the figures are hourly means of the 15-minute prices (changed). `gb` carries the Elexon BMRS
notice. See [elpriser.org](https://elpriser.org) for the details per zone.

## Development

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest                    # 130+ tests against recorded live responses (tests/fixtures)
.venv/bin/python scripts/live_check.py   # a few real requests; prints what the entities would show
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The zone table in `custom_components/elpriser/const.py` mirrors the zone list of the elpriser.org API; a
test compares it with `/api/openapi.json`. The same goes for the Danish grid company list (copied from
the elpriser.org front page): keep both in sync when the service adds a zone or a company.

## Support

Issues and pull requests: <https://github.com/x2q/ha-elpriser/issues>. MIT licensed.
