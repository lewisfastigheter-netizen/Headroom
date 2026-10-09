"""Kolada (RKA), https://api.kolada.se/v3/ (v2 is retired).

Used for what SCB does not publish per municipality: the municipalities' own assessment
of the housing market (Boverket's bostadsmarknadsenkät), unemployment, tax base and the
ready-made peer group "Liknande kommuner socioekonomi, <kommun>, <år>".

Kolada region ids: '0000' Sweden, '00LL' a county (region), 'KKKK' a municipality.
Housing-market KPIs are coded shortage = 0, balance = 1, surplus = 2: zero is a real
value, and a value Kolada marks 'Missing' is null.
"""

from __future__ import annotations

import logging
import re

import polars as pl

from headroom.http import Fetcher

log = logging.getLogger(__name__)

BASE = "https://api.kolada.se/v3"
PEER_GROUP = re.compile(r"^Liknande kommuner socioekonomi, (?P<name>.+), (?P<year>\d{4})$")


def kpi_url(kpi: str) -> str:
    return f"https://www.kolada.se/verktyg/fri-sokning/?kpis={kpi}"


def _pages(f: Fetcher, url: str, params: dict | None = None) -> list[dict]:
    out: list[dict] = []
    next_url: str | None = url
    first = True
    while next_url:
        r = f._get(next_url, params if first else None, None)
        r.raise_for_status()
        js = r.json()
        out.extend(js.get("values", []))
        next_url = js.get("next_url")
        first = False
    return out


def to_region(kolada_id: str) -> tuple[str, str] | None:
    """Kolada id -> (level, SCB code): '0000' riket/00, '0003' lan/03, '0380' kommun/0380."""
    if kolada_id == "0000":
        return "riket", "00"
    if kolada_id.startswith("00") and len(kolada_id) == 4:
        return "lan", kolada_id[2:]
    if len(kolada_id) == 4 and kolada_id.isdigit():
        return "kommun", kolada_id
    return None


def kpi_values(f: Fetcher, kpi: str, years: list[int]) -> pl.DataFrame:
    """All regions for a KPI and years: level, code, period, value (gender T)."""
    rows = []
    url = f"{BASE}/data/kpi/{kpi}/year/{','.join(str(y) for y in years)}"
    for item in _pages(f, url):
        reg = to_region(str(item.get("municipality", "")))
        if reg is None:
            continue
        total = next((v for v in item.get("values", []) if v.get("gender") == "T"), None)
        if total is None:
            continue
        val = total.get("value")
        if total.get("status") == "Missing" or total.get("isdeleted"):
            val = None
        rows.append(
            {
                "level": reg[0],
                "code": reg[1],
                "period": str(item["period"]),
                "value": None if val is None else float(val),
            }
        )
    log.info("Kolada %s: %d values", kpi, len(rows))
    return pl.DataFrame(
        rows,
        schema={"level": pl.Utf8, "code": pl.Utf8, "period": pl.Utf8, "value": pl.Float64},
    )


def peer_groups(f: Fetcher) -> pl.DataFrame:
    """The latest 'Liknande kommuner socioekonomi' group for each municipality:
    columns code (the municipality), group_id, title, year, peer_code, peer_name."""
    groups = _pages(f, f"{BASE}/municipality_groups", {"title": "Liknande kommuner socioekonomi"})
    best: dict[str, dict] = {}
    for g in groups:
        m = PEER_GROUP.match(g.get("title", ""))
        if not m:
            continue
        # The group's own municipality is named in the title; find its code among
        # Kolada's municipality list via the caller (name -> code) if needed.
        key = m.group("name")
        if key not in best or int(m.group("year")) > int(best[key]["year"]):
            best[key] = {**g, "year": m.group("year"), "name": key}
    rows = []
    for name, g in best.items():
        for mem in g.get("members", []):
            rows.append(
                {
                    "name": name,
                    "group_id": g["id"],
                    "title": g["title"],
                    "year": g["year"],
                    "peer_code": mem["member_id"],
                    "peer_name": mem["member_title"],
                }
            )
    log.info("Kolada peer groups: %d municipalities", len(best))
    return pl.DataFrame(
        rows,
        schema={
            "name": pl.Utf8,
            "group_id": pl.Utf8,
            "title": pl.Utf8,
            "year": pl.Utf8,
            "peer_code": pl.Utf8,
            "peer_name": pl.Utf8,
        },
    )
