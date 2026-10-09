"""SCB open geodata (WFS, https://geodata.scb.se/geoserver/stat/wfs).

Layers used:
- stat:RegSO_2025: 3,363 areas, nested in municipalities and counties. The WFS has no
  municipality or county layer, so those borders are built by joining RegSO.
- stat:Tatorter_2023: urban areas (tätorter), used for search and to tell which RegSO
  make up a city.
- stat:befolkning_1km_2025: population on a 1 km grid. Gives population-weighted
  centroids and the population within 50, 100 and 200 km.
- stat:Verksamhetsomraden_2020: business areas (industry, logistics, technical sites)
  with employees and area; the latest edition is 2020.

Everything is requested in SWEREF 99 TM (EPSG:3006, metres), so distances are planar
metres and no projection library is needed; map output is converted to WGS84 with the
Gauss-Krüger formulas published by Lantmäteriet.

This module needs shapely (the `geo` dependency group); the app only reads its output.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from headroom.http import Fetcher

log = logging.getLogger(__name__)

WFS = "https://geodata.scb.se/geoserver/stat/wfs"
GEODATA_PAGE = "https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/"

# --------------------------------------------------------------------------- projection
# GRS80 and SWEREF 99 TM (Lantmäteriet, "Gauss konforma projektion").
_A = 6378137.0
_F = 1 / 298.257222101
_L0 = math.radians(15.0)
_K0 = 0.9996
_FE = 500000.0
_E2 = _F * (2 - _F)
_N = _F / (2 - _F)
_AR = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)


def grid_to_wgs84(east: Any, north: Any) -> tuple[np.ndarray, np.ndarray]:
    """SWEREF 99 TM (E, N in metres) -> (lat, lon) in degrees."""
    e2, n = _E2, _N
    d1 = n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96 - n**4 / 360
    d2 = n**2 / 48 + n**3 / 15 - 437 * n**4 / 1440
    d3 = 17 * n**3 / 480 - 37 * n**4 / 840
    d4 = 4397 * n**4 / 161280
    a_ = e2 + e2**2 + e2**3 + e2**4
    b_ = -(7 * e2**2 + 17 * e2**3 + 30 * e2**4) / 6
    c_ = (224 * e2**3 + 889 * e2**4) / 120
    d_ = -(4279 * e2**4) / 1260
    xi = np.asarray(north, dtype=float) / (_K0 * _AR)
    eta = (np.asarray(east, dtype=float) - _FE) / (_K0 * _AR)
    xp = (
        xi
        - d1 * np.sin(2 * xi) * np.cosh(2 * eta)
        - d2 * np.sin(4 * xi) * np.cosh(4 * eta)
        - d3 * np.sin(6 * xi) * np.cosh(6 * eta)
        - d4 * np.sin(8 * xi) * np.cosh(8 * eta)
    )
    ep = (
        eta
        - d1 * np.cos(2 * xi) * np.sinh(2 * eta)
        - d2 * np.cos(4 * xi) * np.sinh(4 * eta)
        - d3 * np.cos(6 * xi) * np.sinh(6 * eta)
        - d4 * np.cos(8 * xi) * np.sinh(8 * eta)
    )
    phi_s = np.arcsin(np.sin(xp) / np.cosh(ep))
    dl = np.arctan(np.sinh(ep) / np.cos(xp))
    s = np.sin(phi_s)
    lat = phi_s + s * np.cos(phi_s) * (a_ + b_ * s**2 + c_ * s**4 + d_ * s**6)
    return np.degrees(lat), np.degrees(_L0 + dl)


def wgs84_to_grid(lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray]:
    """(lat, lon) in degrees -> SWEREF 99 TM (E, N) in metres."""
    e2, n = _E2, _N
    a_ = e2
    b_ = (5 * e2**2 - e2**3) / 6
    c_ = (104 * e2**3 - 45 * e2**4) / 120
    d_ = (1237 * e2**4) / 1260
    b1 = n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180
    b2 = 13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440
    b3 = 61 * n**3 / 240 - 103 * n**4 / 140
    b4 = 49561 * n**4 / 161280
    phi = np.radians(np.asarray(lat, dtype=float))
    lam = np.radians(np.asarray(lon, dtype=float))
    s = np.sin(phi)
    phi_s = phi - s * np.cos(phi) * (a_ + b_ * s**2 + c_ * s**4 + d_ * s**6)
    dl = lam - _L0
    xp = np.arctan(np.tan(phi_s) / np.cos(dl))
    ep = np.arctanh(np.cos(phi_s) * np.sin(dl))
    north = (
        _K0
        * _AR
        * (
            xp
            + b1 * np.sin(2 * xp) * np.cosh(2 * ep)
            + b2 * np.sin(4 * xp) * np.cosh(4 * ep)
            + b3 * np.sin(6 * xp) * np.cosh(6 * ep)
            + b4 * np.sin(8 * xp) * np.cosh(8 * ep)
        )
    )
    east = (
        _K0
        * _AR
        * (
            ep
            + b1 * np.cos(2 * xp) * np.sinh(2 * ep)
            + b2 * np.cos(4 * xp) * np.sinh(4 * ep)
            + b3 * np.cos(6 * xp) * np.sinh(6 * ep)
            + b4 * np.cos(8 * xp) * np.sinh(8 * ep)
        )
        + _FE
    )
    return east, north


# --------------------------------------------------------------------------- WFS


def features(
    f: Fetcher,
    layer: str,
    *,
    properties: list[str] | None = None,
    srs: str = "EPSG:3006",
    page: int = 2000,
    ttl: timedelta | None = timedelta(days=30),
) -> list[dict]:
    """All features of a layer, paged (GeoServer startIndex/count). With `properties`
    and no geometry attribute named, the geometry is left out of the response."""
    out: list[dict] = []
    start = 0
    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"stat:{layer}",
            "outputFormat": "application/json",
            "srsName": srs,
            "count": str(page),
            "startIndex": str(start),
            "sortBy": "objectid",
        }
        if properties:
            params["propertyName"] = ",".join(properties)
        js = f.get(WFS, params, ttl=ttl, check_robots=False).json()
        feats = js.get("features", [])
        out.extend(feats)
        total = js.get("numberMatched") or js.get("totalFeatures")
        start += len(feats)
        if not feats or (isinstance(total, int) and start >= total) or len(feats) < page:
            break
    log.info("WFS %s: %d features", layer, len(out))
    return out


# --------------------------------------------------------------------------- geometry


def _shape(geom: dict):
    from shapely.geometry import shape

    return shape(geom)


def _to_wgs84_geojson(geom, digits: int = 5) -> dict:
    """shapely geometry in SWEREF 99 TM -> GeoJSON geometry dict in WGS84."""
    from shapely import get_coordinates, set_coordinates
    from shapely.geometry import mapping

    xy = get_coordinates(geom)
    lat, lon = grid_to_wgs84(xy[:, 0], xy[:, 1])
    g = set_coordinates(geom, np.column_stack([np.round(lon, digits), np.round(lat, digits)]))
    return mapping(g)


def _feature_collection(items: list[tuple[str, Any, dict]], tol: float) -> dict:
    from shapely import make_valid

    feats = []
    for code, geom, props in items:
        g = make_valid(geom.simplify(tol, preserve_topology=True))
        if g.is_empty:
            continue
        feats.append(
            {
                "type": "Feature",
                "id": code,
                "properties": {"code": code, **props},
                "geometry": _to_wgs84_geojson(g),
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def build(f: Fetcher, out_dir: Path) -> dict[str, Any]:
    """Fetch the layers, write simplified GeoJSON under `out_dir`, and return plain
    Python data for the pipeline:

    regso:   list of {code, name, kommun, lan, area_km2, cx, cy, lat, lon, cells}
    kommun:  {code: {cx, cy, lat, lon, area_km2}}   (population-weighted where possible)
    lan:     {code: {cx, cy, lat, lon}}
    tatort:  list of {code, name, kommun, pop, year, lat, lon, regso: [codes]}
    grid:    (east, north, pop, pop_20_34) numpy arrays of 1 km cell centres
    business_areas: {kommun: {employees, workplaces, area_ha, areas}}
    """
    from shapely import STRtree, points, union_all

    # 1 km population grid: properties only (cell id gives the lower-left corner)
    grid_feats = features(
        f,
        "befolkning_1km_2025",
        properties=["rutid_scb", "beftotalt", "ald20_24", "ald25_29", "ald30_34"],
        page=20000,
    )
    ge, gn, gp, gy = [], [], [], []
    for ft in grid_feats:
        p = ft["properties"]
        rid = str(p["rutid_scb"])
        ge.append(int(rid[:6]) + 500)
        gn.append(int(rid[6:]) + 500)
        gp.append(p.get("beftotalt") or 0)
        gy.append(sum(p.get(k) or 0 for k in ("ald20_24", "ald25_29", "ald30_34")))
    grid = (np.array(ge, float), np.array(gn, float), np.array(gp, float), np.array(gy, float))

    # RegSO 2025 polygons
    reg_feats = features(f, "RegSO_2025", page=1000)
    regso, geoms = [], []
    for ft in reg_feats:
        p = ft["properties"]
        g = _shape(ft["geometry"])
        geoms.append(g)
        regso.append(
            {
                "code": p["regsokod"],
                "name": p["regsonamn"],
                "kommun": p["kommunkod"],
                "lan": p["lanskod"],
                "area_km2": g.area / 1e6,
            }
        )
    tree = STRtree(geoms)
    pts = points(np.column_stack([grid[0], grid[1]]))
    pi, gi = tree.query(pts, predicate="within")
    owner = np.full(len(pts), -1)
    owner[pi] = gi
    for i, r in enumerate(regso):
        mask = owner == i
        w = grid[2][mask]
        if mask.sum() >= 3 and w.sum() > 0:
            cx = float((grid[0][mask] * w).sum() / w.sum())
            cy = float((grid[1][mask] * w).sum() / w.sum())
        else:  # small urban areas hold few 1 km cell centres: use the shape itself
            c = geoms[i].representative_point() if not geoms[i].is_empty else None
            cx, cy = (c.x, c.y) if c is not None else (math.nan, math.nan)
        r.update(cx=cx, cy=cy, cells=int(mask.sum()))
    lat, lon = grid_to_wgs84([r["cx"] for r in regso], [r["cy"] for r in regso])
    for r, la, lo in zip(regso, lat, lon, strict=True):
        r["lat"], r["lon"] = float(la), float(lo)

    # municipalities and counties: union of RegSO; centroids weighted by grid population
    by_k: dict[str, list[int]] = {}
    for i, r in enumerate(regso):
        by_k.setdefault(r["kommun"], []).append(i)
    kommun_geom = {k: union_all([geoms[i] for i in idx]) for k, idx in by_k.items()}
    kommun: dict[str, dict] = {}
    for k, idx in by_k.items():
        mask = np.isin(owner, idx)
        w = grid[2][mask]
        if w.sum() > 0:
            cx = float((grid[0][mask] * w).sum() / w.sum())
            cy = float((grid[1][mask] * w).sum() / w.sum())
        else:
            c = kommun_geom[k].representative_point()
            cx, cy = c.x, c.y
        kommun[k] = {"cx": cx, "cy": cy, "area_km2": kommun_geom[k].area / 1e6}
    lan_geom: dict[str, Any] = {}
    by_l: dict[str, list[str]] = {}
    for k in kommun:
        by_l.setdefault(k[:2], []).append(k)
    lan: dict[str, dict] = {}
    for code, ks in by_l.items():
        lan_geom[code] = union_all([kommun_geom[k] for k in ks])
        mask = np.isin(owner, [i for k in ks for i in by_k[k]])
        w = grid[2][mask]
        lan[code] = {
            "cx": float((grid[0][mask] * w).sum() / w.sum()),
            "cy": float((grid[1][mask] * w).sum() / w.sum()),
        }
    for d in (kommun, lan):
        la, lo = grid_to_wgs84([v["cx"] for v in d.values()], [v["cy"] for v in d.values()])
        for v, a, b in zip(d.values(), la, lo, strict=True):
            v["lat"], v["lon"] = float(a), float(b)

    # urban areas (tätorter) and which RegSO make them up
    tat_feats = features(f, "Tatorter_2023", page=1000)
    tatort = []
    for ft in tat_feats:
        p = ft["properties"]
        g = _shape(ft["geometry"])
        c = g.representative_point()
        la, lo = grid_to_wgs84([c.x], [c.y])
        # A RegSO belongs to the city when its centroid lies inside the city, or when at
        # least 30% of its area does (large edge areas around a small town).
        members = []
        for j in tree.query(g, predicate="intersects"):
            r, rg = regso[j], geoms[j]
            inside = g.contains(points(r["cx"], r["cy"]))
            share = rg.intersection(g).area / rg.area if rg.area else 0
            if inside or share >= 0.30:
                members.append(r["code"])
        tatort.append(
            {
                "code": p["tatortskod"],
                "name": (p.get("tatort") or "").strip(),
                "kommun": p["kommun"],
                "pop": p.get("bef"),
                "year": p.get("ar"),
                "lat": float(la[0]),
                "lon": float(lo[0]),
                "regso": members,
            }
        )

    # business areas (verksamhetsområden 2020), summed per municipality
    biz = features(
        f,
        "Verksamhetsomraden_2020",
        properties=["vo_kod", "kommunkod", "anstallda", "arbetsstallen", "area_ha"],
        page=5000,
    )
    business: dict[str, dict] = {}
    for ft in biz:
        p = ft["properties"]
        b = business.setdefault(
            p["kommunkod"], {"employees": 0, "workplaces": 0, "area_ha": 0.0, "areas": 0}
        )
        b["employees"] += p.get("anstallda") or 0
        b["workplaces"] += p.get("arbetsstallen") or 0
        b["area_ha"] += p.get("area_ha") or 0
        b["areas"] += 1

    # GeoJSON: Sweden by municipality and county (coarse), RegSO per municipality (fine)
    geo = out_dir / "geo"
    _write(
        geo / "kommun.geojson",
        _feature_collection([(k, g, {}) for k, g in sorted(kommun_geom.items())], tol=400),
    )
    _write(
        geo / "lan.geojson",
        _feature_collection([(k, g, {}) for k, g in sorted(lan_geom.items())], tol=800),
    )
    for k, idx in by_k.items():
        items = [(regso[i]["code"], geoms[i], {"name": regso[i]["name"]}) for i in idx]
        _write(geo / "regso" / f"{k}.geojson", _feature_collection(items, tol=12))
    return {
        "regso": regso,
        "kommun": kommun,
        "lan": lan,
        "tatort": tatort,
        "grid": grid,
        "business_areas": business,
    }


def within(cx: np.ndarray, cy: np.ndarray, grid: tuple, radius_m: float) -> np.ndarray:
    """Population within `radius_m` (straight line) of each point, from the 1 km grid."""
    ge, gn, gp = grid[0], grid[1], grid[2]
    out = np.zeros(len(cx))
    step = 64
    for i in range(0, len(cx), step):
        dx = ge[None, :] - np.asarray(cx[i : i + step])[:, None]
        dy = gn[None, :] - np.asarray(cy[i : i + step])[:, None]
        inside = dx * dx + dy * dy <= radius_m * radius_m
        out[i : i + step] = inside @ gp
    return out
