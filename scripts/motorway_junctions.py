"""One-off: motorway junctions on E4, E6, E18 and E20 from OpenStreetMap.

Writes config/geo/motorway_junctions.csv (lat, lon, ref, name, osm_id). The pipeline
only reads the CSV, so this runs again only when the list should be refreshed:

    uv run python scripts/motorway_junctions.py

Data © OpenStreetMap contributors, ODbL. Queried through a public Overpass instance in
small tiles with retries, because large country-wide queries time out.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "geo" / "motorway_junctions.csv"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "Headroom/0.1 (personal research project; Nordic property credit monitor)"

# Junction nodes sit on motorway ways; the European route number is on the way
# (ref / int_ref) or on the route relation. Ask for junctions with their parent ways.
QUERY = """[out:json][timeout:120][bbox:{bbox}];
way["highway"="motorway"]["ref"~"(^|;) ?E ?(4|6|18|20)( ?;|$)"]->.w;
node(w.w)["highway"="motorway_junction"];
out;"""


def tiles(step: float = 0.5):
    """Sweden's motorways lie between 55 and 66 degrees north, 11 and 24 east. Small
    tiles keep each query light enough for the public Overpass servers."""
    # (west, east, southernmost, northernmost) for each column; the rest is sea or Norway
    cols = [
        (11.0, 14.5, 55.0, 61.0),
        (14.5, 18.0, 55.0, 66.0),
        (18.0, 21.0, 58.5, 66.0),
        (21.0, 24.2, 63.5, 66.0),
    ]
    lat = 55.0
    while lat < 66.0:
        for lo, hi, south, north in cols:
            if south <= lat < north:
                yield f"{lat:.1f},{lo:.1f},{lat + step:.1f},{hi:.1f}"
        lat += step


CACHE = ROOT / "data" / "cache" / "overpass"


def fetch(bbox: str) -> list[dict]:
    """One tile, cached on disk so an interrupted run resumes where it stopped."""
    cached = CACHE / f"{bbox}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    els = _fetch(bbox)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(els))
    return els


def _fetch(bbox: str) -> list[dict]:
    for _attempt in range(20):
        for url in ENDPOINTS:
            try:
                r = httpx.post(
                    url,
                    data={"data": QUERY.format(bbox=bbox)},
                    headers={"User-Agent": UA},
                    timeout=240,
                )
                if r.status_code == 200:
                    return r.json().get("elements", [])
                print(f"  {bbox} {url} HTTP {r.status_code}", file=sys.stderr)
            except httpx.HTTPError as e:
                print(f"  {bbox} {url} {e}", file=sys.stderr)
        time.sleep(10)
    raise RuntimeError(f"Overpass failed for {bbox}")


def main() -> None:
    rows: dict[int, dict] = {}
    for bbox in tiles():
        els = fetch(bbox)
        for e in els:
            t = e.get("tags", {})
            rows[e["id"]] = {
                "osm_id": e["id"],
                "lat": round(e["lat"], 5),
                "lon": round(e["lon"], 5),
                "ref": t.get("ref", ""),
                "name": t.get("name", ""),
            }
        print(f"{bbox}: {len(els)} junctions ({len(rows)} total)")
        time.sleep(5)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["osm_id", "lat", "lon", "ref", "name"])
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["lat"], r["lon"])):
            w.writerow(r)
    print(f"Wrote {len(rows)} junctions to {OUT}")


if __name__ == "__main__":
    main()
