"""Constants and static tables for the Elpriser integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

DOMAIN: Final = "elpriser"

API_BASE: Final = "https://elpriser.org/api"
API_TIMEOUT: Final = 30  # seconds
WEBSITE: Final = "https://elpriser.org"

CONF_ZONE: Final = "zone"
CONF_MODE: Final = "mode"
CONF_GLN: Final = "gln"
CONF_CHEAP_HOURS: Final = "cheap_hours"
CONF_SHOW_FORECAST: Final = "show_forecast"

DEFAULT_CHEAP_HOURS: Final = 4
MIN_CHEAP_HOURS: Final = 1
MAX_CHEAP_HOURS: Final = 12
DEFAULT_SHOW_FORECAST: Final = True

# --- Polling etiquette -------------------------------------------------------
# elpriser.org is a free service that is edge-cached for 30-60 minutes. One
# request per config entry per hour is plenty: the whole day set (today,
# tomorrow once published, the forecast days) comes in a single response and
# the "current hour" is picked locally at every hour boundary.
REFRESH_INTERVAL: Final = timedelta(minutes=60)
# While tomorrow is not published yet in the window after the day-ahead
# auction results (13:00 CET), look a little more often. Still cached at the edge.
CATCH_UP_INTERVAL: Final = timedelta(minutes=20)
CATCH_UP_START_HOUR: Final = 13  # Europe/Copenhagen wall clock
CATCH_UP_END_HOUR: Final = 18
# Backoff after an error: 5, 10, 20, 40 minutes, then the normal interval.
BACKOFF_BASE: Final = timedelta(minutes=5)
# The explicit "tomorrow is published" refresh: 13:05 CET plus random jitter so
# that not every installation asks in the same second.
PUBLISH_TIME_TZ: Final = "Europe/Copenhagen"
PUBLISH_HOUR: Final = 13
PUBLISH_MINUTE: Final = 5
PUBLISH_JITTER_SECONDS: Final = 300
# A source that is only known for the hours that have already passed (gb) may
# keep showing its last complete hour for at most this long.
LATEST_KNOWN_MAX_AGE: Final = timedelta(hours=3)

# --- Denmark -----------------------------------------------------------------
# Price modes of /api/forecast (see /api/openapi.json). DKK/kWh.
MODE_INKL_ALT: Final = "inkl_alt"
DK_MODES: Final = (
    "inkl_alt",
    "spot_inkl",
    "spot_ex",
    "inkl_alt_minus",
    "inkl_alt_elvarme",
    "net_inkl_alt",
    "net_inkl_alt_elvarme",
    "net_inkl_tarif",
)
# These need a grid company (GLN).
NET_MODES: Final = frozenset({"net_inkl_alt", "net_inkl_alt_elvarme", "net_inkl_tarif"})

# Danish grid companies: (name, GLN). Copied from the NETS table in the
# elpriser.org front page (index.html) - KEEP IN SYNC when companies are added.
# The config flow also accepts a free-typed GLN for anything not listed.
DK_GRID_COMPANIES: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "DK1": (
        ("N1", "5790001089030"),
        ("Trefor", "5790000392261"),
        ("Konstant", "5790000704842"),
        ("Dinel", "5790000610099"),
        ("Vores Elnet", "5790000610976"),
        ("Ravdex", "5790000836727"),
        ("RAH Net", "5790000681327"),
        ("Elværk", "5790000681358"),
        ("Nord Energi", "5790000610877"),
        ("NOE Net", "5790000395620"),
        ("Elnet Midt", "5790001100520"),
        ("Flow Elnet", "5790000392551"),
        ("LNet", "5790001090111"),
        ("Veksel", "5790001088217"),
        ("Ikast El Net", "5790000682102"),
        ("Midtfyns Elforsyning", "5790001089023"),
        ("Elinord", "5790001095277"),
        ("Læsø Elnet", "5790001103460"),
        ("Sunds Net", "5790001095444"),
        ("Tarm Elværk", "5790000706419"),
        ("Hammel Elforsyning", "5790001090166"),
        ("Grindsted Elnet", "5790000681105"),
        ("Forsyning Elnet", "5790001088309"),
        ("Aal El-Net", "5790001095451"),
        ("Hjerting", "5790001095376"),
        ("Hurup Elværk", "5790000610839"),
    ),
    "DK2": (
        ("Cerius", "5790000705184"),
        ("Trefor Øst", "5790000706686"),
        ("Radius", "5790000705689"),
        ("Elektrus", "5790000836239"),
        ("Zeanet", "5790001089375"),
    ),
}


def grid_company_name(gln: str | None) -> str | None:
    """Name of a known Danish grid company, or None."""
    if not gln:
        return None
    for companies in DK_GRID_COMPANIES.values():
        for name, known in companies:
            if known == gln:
                return name
    return None


# --- Zones -------------------------------------------------------------------
# The 40 zones served by https://elpriser.org/api/nordic?zone=<key> (plus the
# Danish /api/forecast for dk1/dk2). KEEP IN SYNC with NORDIC_ZONES and EU_ZONES
# in the elpriser.org API (functions/api/[[catchall]].js); tests/test_zones.py
# compares this table with the live /api/openapi.json zone enum.
#
# tz is the wall clock the API's "hour" field (0-23) is expressed in:
#  * the 13 Nordic feed zones (dk, no, se, fi, nl) are all Europe/Copenhagen -
#    that includes Finland: the feed floors every ENTSO-E slot to the
#    Copenhagen hour, so fi's "hour 0" is 00:00 CET, not 00:00 Helsinki;
#  * every other zone is its own country's wall clock (Lisbon, Athens, ...).
# DST: a 25-hour day carries one value for the doubled local hour 2 (the mean
# of both), a 23-hour day has no hour 2 (see prices.expand_day).

GROUP_DK: Final = "dk"
GROUP_NORDIC: Final = "nordic"
GROUP_EU: Final = "eu"

# Licence/attribution notes of the underlying price sources, by Zone.source.
SOURCE_NOTE_ELEXON: Final = "Contains BMRS data © Elexon Limited copyright and database right {year}"
SOURCE_NOTE_ENERGY_CHARTS: Final = (
    "Price data: Bundesnetzagentur | SMARD.de via Energy-Charts.info, CC BY 4.0; "
    "hourly means of the 15-minute prices (changed)"
)
ATTRIBUTION: Final = "Data from elpriser.org"


@dataclass(frozen=True, slots=True)
class Zone:
    """One selectable price zone."""

    key: str
    label: str
    tz: str
    group: str
    area: str | None = None  # DK1 / DK2 for the Danish zones
    source: str | None = None  # "elexon" | "energy_charts" for licence notes
    latest_known: bool = False  # only past hours are priced (within-day index)


_CPH = "Europe/Copenhagen"


def _z(key: str, label: str, tz: str, group: str, **kw) -> tuple[str, Zone]:
    return key, Zone(key=key, label=label, tz=tz, group=group, **kw)


ZONES: Final[dict[str, Zone]] = dict(
    [
        _z("dk1", "Denmark - DK1 West (Jutland, Funen)", _CPH, GROUP_DK, area="DK1"),
        _z("dk2", "Denmark - DK2 East (Zealand, Bornholm)", _CPH, GROUP_DK, area="DK2"),
        _z("no1", "Norway - NO1 Oslo", _CPH, GROUP_NORDIC),
        _z("no2", "Norway - NO2 Kristiansand", _CPH, GROUP_NORDIC),
        _z("no3", "Norway - NO3 Trondheim", _CPH, GROUP_NORDIC),
        _z("no4", "Norway - NO4 Tromsø", _CPH, GROUP_NORDIC),
        _z("no5", "Norway - NO5 Bergen", _CPH, GROUP_NORDIC),
        _z("se1", "Sweden - SE1 Luleå", _CPH, GROUP_NORDIC),
        _z("se2", "Sweden - SE2 Sundsvall", _CPH, GROUP_NORDIC),
        _z("se3", "Sweden - SE3 Stockholm", _CPH, GROUP_NORDIC),
        _z("se4", "Sweden - SE4 Malmö", _CPH, GROUP_NORDIC),
        _z("fi", "Finland", _CPH, GROUP_NORDIC),
        _z("nl", "Netherlands", _CPH, GROUP_NORDIC),
        _z("de", "Germany and Luxembourg (DE-LU)", "Europe/Berlin", GROUP_EU, source="energy_charts"),
        _z("fr", "France", "Europe/Paris", GROUP_EU, source="energy_charts"),
        _z("pl", "Poland", "Europe/Warsaw", GROUP_EU, source="energy_charts"),
        _z("it-n", "Italy - North (IT-North)", "Europe/Rome", GROUP_EU, source="energy_charts"),
        _z("at", "Austria", "Europe/Vienna", GROUP_EU, source="energy_charts"),
        _z("be", "Belgium", "Europe/Brussels", GROUP_EU, source="energy_charts"),
        _z("ch", "Switzerland", "Europe/Zurich", GROUP_EU, source="energy_charts"),
        _z("cz", "Czechia", "Europe/Prague", GROUP_EU, source="energy_charts"),
        _z("hu", "Hungary", "Europe/Budapest", GROUP_EU, source="energy_charts"),
        _z("si", "Slovenia", "Europe/Ljubljana", GROUP_EU, source="energy_charts"),
        _z("es", "Spain", "Europe/Madrid", GROUP_EU),
        _z("pt", "Portugal", "Europe/Lisbon", GROUP_EU),
        _z("sk", "Slovakia", "Europe/Bratislava", GROUP_EU),
        _z("gr", "Greece", "Europe/Athens", GROUP_EU),
        _z("ro", "Romania", "Europe/Bucharest", GROUP_EU),
        _z("bg", "Bulgaria", "Europe/Sofia", GROUP_EU),
        _z("hr", "Croatia", "Europe/Zagreb", GROUP_EU),
        _z("rs", "Serbia", "Europe/Belgrade", GROUP_EU),
        _z("ee", "Estonia", "Europe/Tallinn", GROUP_EU),
        _z("lv", "Latvia", "Europe/Riga", GROUP_EU),
        _z("lt", "Lithuania", "Europe/Vilnius", GROUP_EU),
        _z("me", "Montenegro", "Europe/Podgorica", GROUP_EU),
        _z("mk", "North Macedonia", "Europe/Skopje", GROUP_EU),
        _z("al", "Albania", "Europe/Tirane", GROUP_EU),
        _z("xk", "Kosovo", "Europe/Belgrade", GROUP_EU),
        _z(
            "gb",
            "Great Britain (within-day market index, not day-ahead)",
            "Europe/London",
            GROUP_EU,
            source="elexon",
            latest_known=True,
        ),
        _z("ie", "Ireland (all-island market)", "Europe/Dublin", GROUP_EU),
    ]
)

assert len(ZONES) == 40  # noqa: S101 - cheap guard against a botched edit
