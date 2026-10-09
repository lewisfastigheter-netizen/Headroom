"""County (län) and city for each company.

Reference data in config/geo:
- kommuner.csv: Sweden's 290 municipalities and their county, from Statistics
  Sweden's API (api.scb.se, table BE0101A).
- postcode_county.csv: the county most addresses with each three-digit postcode
  prefix belong to, derived from Bolagsverket's bulk file (addresses whose postal
  town is also a municipality name). Used when a postal town is not a municipality.

Private companies are placed by their registered address. Listed companies and bond
issuers are placed where the largest share of their portfolio is, when the report's
regional split names a Swedish place; otherwise by their registered address.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import polars as pl

from headroom.config import CONFIG_DIR

GEO = CONFIG_DIR / "geo"

# Report region names that are not municipality names.
ALIASES = {
    "gothenburg": "Göteborg",
    "goteborg": "Göteborg",
    "trestadsregionen": "Göteborg",
    "copenhagen": None,
    "malmo": "Malmö",
    "orebro": "Örebro",
    "vasteras": "Västerås",
    "jonkoping": "Jönköping",
    "norrkoping": "Norrköping",
    "linkoping": "Linköping",
    "vaxjo": "Växjö",
    "gavle": "Gävle",
    "umea": "Umeå",
    "lulea": "Luleå",
}


@lru_cache
def _table() -> dict[str, tuple[str, str]]:
    """lower-case municipality -> (municipality, county)"""
    df = pl.read_csv(GEO / "kommuner.csv", schema_overrides={"kommun_code": pl.Utf8})
    return {r["kommun"].lower(): (r["kommun"], r["county"]) for r in df.to_dicts()}


def _kommuner() -> dict[str, str]:
    return {k: v[1] for k, v in _table().items()}


@lru_cache
def _prefixes() -> dict[str, str]:
    df = pl.read_csv(GEO / "postcode_county.csv", schema_overrides={"prefix": pl.Utf8})
    return {r["prefix"]: r["county"] for r in df.to_dicts()}


def city_name(postort: str | None) -> str | None:
    if not postort or not postort.strip():
        return None
    return " ".join(
        w.capitalize() if w.isupper() or w.islower() else w
        for w in re.split(r"\s+", postort.strip())
    )


def from_address(postort: str | None, postnummer: str | None) -> tuple[str | None, str | None]:
    """(county, city) for a Swedish postal address."""
    city = city_name(postort)
    county = _kommuner().get((city or "").lower())
    pnr = re.sub(r"\D", "", postnummer or "")
    if county is None and len(pnr) == 5:
        county = _prefixes().get(pnr[:3])
    return county, city


def _place(region: str) -> tuple[str | None, str | None]:
    """A report's region label -> (county, municipality) when it names a Swedish place."""
    text = region.lower()
    for alias, kommun in ALIASES.items():
        if alias in text:
            return (_kommuner().get(kommun.lower()), kommun) if kommun else (None, None)
    best = None
    for low, (kommun, county) in _table().items():
        hit = re.search(rf"(?<!\w){re.escape(low)}(?!\w)", text)
        if hit and (best is None or len(kommun) > len(best[1])):
            best = (county, kommun)
    return best or (None, None)


def from_regions(region_mix: str | None) -> tuple[str | None, str | None]:
    """(county, city) of the largest region in a report's regional split, if it is a
    Swedish municipality; (None, None) for countries and unnamed regions."""
    try:
        mix = json.loads(region_mix or "{}")
    except ValueError:
        return None, None
    if not mix:
        return None, None
    return _place(max(mix, key=mix.get))


def short_county(county: str | None) -> str | None:
    """'Stockholms län' -> 'Stockholm', 'Västra Götalands län' -> 'Västra Götaland'."""
    if not county:
        return None
    s = county.removesuffix(" län")
    return s[:-1] if s.endswith("s") and not s.endswith("ss") else s
