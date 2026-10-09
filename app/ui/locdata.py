"""Cached access to the location snapshot (data/snapshots/locations).

The snapshot is public statistics and is the same in live and demo mode. Features and
scores are derived here from the indicator table and config/weights.yaml, so an edit to
the weights shows on the next rerun, as for the company scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import polars as pl
import streamlit as st

from headroom.config import weights as load_weights
from headroom.model import location as L
from headroom.store.db import open_snapshot, snapshot_dir

LOC_DIR = snapshot_dir("locations")
GEO = LOC_DIR / "geo"


@dataclass
class Locations:
    meta: dict
    loc: pl.DataFrame
    ind: pl.DataFrame
    feats: pl.DataFrame
    scores: pl.DataFrame  # wide: code, residential, logistics (+ components json)
    peers: pl.DataFrame
    cfg: dict

    @property
    def available(self) -> bool:
        return self.loc.height > 0

    def place(self, code: str) -> dict | None:
        r = self.loc.filter(pl.col("code") == code)
        return r.row(0, named=True) if r.height else None

    def feat(self, code: str) -> dict:
        r = self.feats.filter(pl.col("code") == code)
        return r.row(0, named=True) if r.height else {}

    def score(self, code: str, kind: str) -> float | None:
        r = self.scores.filter(pl.col("code") == code)
        return r[kind][0] if r.height and kind in r.columns else None

    def components(self, code: str, kind: str) -> list[dict]:
        r = self.scores.filter(pl.col("code") == code)
        col = f"{kind}_components"
        return json.loads(r[col][0]) if r.height and r[col][0] else []

    def series(self, name: str, codes: list[str]) -> pl.DataFrame:
        return self.ind.filter(
            (pl.col("indicator") == name)
            & pl.col("code").is_in(codes)
            & pl.col("value").is_not_null()
        ).sort("period")

    def source(self, name: str) -> dict:
        """Source table, URL, latest period and as-of for one indicator."""
        r = self.ind.filter((pl.col("indicator") == name) & pl.col("value").is_not_null())
        if r.is_empty():
            return {}
        top = r.sort("period").tail(1).row(0, named=True)
        return {
            k: top[k] for k in ("source", "source_table", "source_url", "as_of", "unit", "period")
        }

    def name(self, code: str) -> str:
        p = self.place(code)
        return p["name"] if p else code


def _empty() -> Locations:
    return Locations(
        {}, pl.DataFrame(), pl.DataFrame(), pl.DataFrame(), pl.DataFrame(), pl.DataFrame(), {}
    )


@st.cache_data(show_spinner=False)
def _load(generated_at: str | None) -> tuple[dict, dict[str, pl.DataFrame]]:
    snap = open_snapshot("locations")
    t = {n: snap.table(n) for n in ("location", "location_indicator", "location_peer")}
    return snap.meta, t


@st.cache_data(show_spinner="Reading location statistics…")
def _derive(generated_at: str | None, weights_key: str, _loc: pl.DataFrame, _ind: pl.DataFrame):
    cfg = json.loads(weights_key)
    feats = L.features(_loc, _ind, L.load_utsatta())
    long = L.score_all(feats, cfg)
    wide = long.pivot(on="score_kind", index="code", values="score").join(
        long.pivot(on="score_kind", index="code", values="components_json").rename(
            {"residential": "residential_components", "logistics": "logistics_components"},
            strict=False,
        ),
        on="code",
        how="left",
    )
    for c in ("residential", "logistics", "residential_components", "logistics_components"):
        if c not in wide.columns:
            wide = wide.with_columns(pl.lit(None).alias(c))
    return feats, wide


def get_locations() -> Locations:
    if not (LOC_DIR / "location.parquet").exists():
        return _empty()
    meta_p = LOC_DIR / "meta.json"
    gen = json.loads(meta_p.read_text("utf-8")).get("generated_at") if meta_p.exists() else None
    meta, t = _load(gen)
    cfg = load_weights()
    feats, scores = _derive(
        gen, json.dumps(cfg, sort_keys=True), t["location"], t["location_indicator"]
    )
    return Locations(
        meta, t["location"], t["location_indicator"], feats, scores, t["location_peer"], cfg
    )


@st.cache_data(show_spinner=False)
def place_index(generated_at: str | None) -> list[dict]:
    """Search index of places, built once per snapshot."""
    snap = open_snapshot("locations")
    return L.place_index(snap.table("location"))


def index_for_session() -> list[dict]:
    meta_p = LOC_DIR / "meta.json"
    if not meta_p.exists():
        return []
    gen = json.loads(meta_p.read_text("utf-8")).get("generated_at")
    return place_index(gen)


@lru_cache(maxsize=64)
def geojson(name: str) -> dict | None:
    """'kommun', 'lan' or 'regso/<kommun code>'."""
    p: Path = GEO / f"{name}.geojson"
    return json.loads(p.read_text("utf-8")) if p.exists() else None
