"""GLEIF: LEI to legal name, legal form and Swedish organisation number.

API docs: https://www.gleif.org/en/lei-data/gleif-api
Swedish entities registered with Bolagsverket carry registration authority
RA000544 and their organisationsnummer in `registeredAs`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

import polars as pl

from headroom.http import Fetcher
from headroom.schemas import normalise_org_nr

API = "https://api.gleif.org/api/v1"
BOLAGSVERKET_RA = "RA000544"
CHUNK = 100  # LEIs per request, keeps the URL well under server limits

SCHEMA = {
    "lei": pl.Utf8,
    "legal_name": pl.Utf8,
    "legal_form": pl.Utf8,
    "country": pl.Utf8,
    "city": pl.Utf8,
    "org_nr": pl.Utf8,
    "entity_status": pl.Utf8,
    "lei_status": pl.Utf8,
    "source_url": pl.Utf8,
}


def _row(rec: dict) -> dict:
    a = rec["attributes"]
    e = a["entity"]
    reg_at = (e.get("registeredAt") or {}).get("id")
    org = None
    if e.get("registeredAs") and reg_at == BOLAGSVERKET_RA:
        try:
            org = normalise_org_nr(e["registeredAs"])
        except ValueError:
            org = None
    return {
        "lei": a["lei"],
        "legal_name": e["legalName"]["name"],
        "legal_form": (e.get("legalForm") or {}).get("id"),
        "country": e["legalAddress"]["country"],
        "city": e["legalAddress"].get("city"),
        "org_nr": org,
        "entity_status": e.get("status"),
        "lei_status": a["registration"]["status"],
        "source_url": f"{API}/lei-records/{a['lei']}",
    }


def lookup_leis(f: Fetcher, leis: Iterable[str]) -> pl.DataFrame:
    """Batch lookup. Unknown LEIs are simply absent from the result."""
    unique = sorted(set(leis))
    rows: list[dict] = []
    for i in range(0, len(unique), CHUNK):
        chunk = unique[i : i + CHUNK]
        params = {"filter[lei]": ",".join(chunk), "page[size]": str(CHUNK)}
        data = f.get(f"{API}/lei-records", params=params, ttl=timedelta(days=7)).json()
        rows.extend(_row(r) for r in data["data"])
    return pl.DataFrame(rows, schema=SCHEMA)


def search_name(f: Fetcher, name: str, country: str = "SE") -> pl.DataFrame:
    params = {
        "filter[entity.legalName]": name,
        "filter[entity.legalAddress.country]": country,
        "page[size]": "20",
    }
    data = f.get(f"{API}/lei-records", params=params, ttl=timedelta(days=7)).json()
    return pl.DataFrame([_row(r) for r in data["data"]], schema=SCHEMA)
