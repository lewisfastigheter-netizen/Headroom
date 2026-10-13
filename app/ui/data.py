"""Cached access to the active snapshot and its scores."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import polars as pl
import streamlit as st

from headroom.config import resolve_mode
from headroom.config import weights as load_weights
from headroom.model import geo
from headroom.model.scoring import ScoreCard, reference_date, score_all, score_frame
from headroom.store import private
from headroom.store.db import TABLES, open_snapshot, snapshot_dir


@dataclass
class Data:
    mode: str
    meta: dict
    ref: date
    t: dict[str, pl.DataFrame]
    cards: dict[str, ScoreCard]
    scores: pl.DataFrame  # one row per company, joined with company fields
    cfg: dict

    @property
    def fictional(self) -> bool:
        return bool(self.meta.get("fictional"))

    def as_of(self, table: str) -> str | None:
        return (self.meta.get("as_of") or {}).get(table)

    def source_label(self, table: str) -> str:
        """Short provenance line for charts and KPIs."""
        when = self.as_of(table) or "unknown date"
        if self.fictional:
            return f"Source: fictional demo dataset, as of {when}"
        return f"As of {when}"

    def company(self, org_nr: str) -> dict:
        return self.t["company"].filter(pl.col("org_nr") == org_nr).row(0, named=True)


# cache_resource, not cache_data: cache_data unpickles a fresh copy of the result on every
# rerun of every page. The frames are immutable and the views only read them, so one shared
# copy is safe. max_entries: a new private_found.jsonl or snapshot replaces the old entry.
@st.cache_resource(show_spinner=False, max_entries=2)
def _load(
    mode: str, generated_at: str | None, private_mtime: float = 0.0
) -> tuple[dict, dict[str, pl.DataFrame]]:
    snap = open_snapshot(mode)
    t = {name: snap.table(name) for name in TABLES}
    if "value_basis" not in t["financials"].columns:  # snapshots written before the field
        t["financials"] = t["financials"].with_columns(pl.lit(None, pl.Utf8).alias("value_basis"))
    for c in ("county", "city"):  # snapshots written before location was recorded
        if c not in t["company"].columns:
            t["company"] = t["company"].with_columns(pl.lit(None, pl.Utf8).alias(c))
    if mode == "live":  # private companies from the daily Bolagsverket job
        t = private.merge(t)
    t["company"] = _place(t["company"])
    return snap.meta, t


def _place(company: pl.DataFrame) -> pl.DataFrame:
    """Listed companies and bond issuers sit where most of their portfolio is, when the
    report names a Swedish place; otherwise at their registered address."""
    placed = [geo.from_regions(m) for m in company["region_mix"].to_list()]
    return company.with_columns(
        pl.Series(
            "county",
            [p[0] or c for p, c in zip(placed, company["county"], strict=True)],
            dtype=pl.Utf8,
        ),
        pl.Series(
            "city", [p[1] or c for p, c in zip(placed, company["city"], strict=True)], dtype=pl.Utf8
        ),
    )


def available_modes() -> list[str]:
    modes = ["live"] if (snapshot_dir("live") / "bond.parquet").exists() else []
    return [*modes, "demo"]


def current_mode() -> str:
    mode = st.session_state.get("mode") or st.query_params.get("data") or resolve_mode()
    return mode if mode in available_modes() else available_modes()[0]


def _generated_at(mode: str) -> str | None:
    """generated_at of a snapshot, from meta.json (no DuckDB connection per rerun)."""
    p = snapshot_dir(mode) / "meta.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    return _meta_gen(str(p), mtime)


@lru_cache(maxsize=8)
def _meta_gen(path: str, mtime: float) -> str | None:
    return json.loads(Path(path).read_text("utf-8")).get("generated_at")


def get_data() -> Data:
    mode = current_mode()
    mtime = private.PRIVATE_FOUND.stat().st_mtime if private.PRIVATE_FOUND.exists() else 0.0
    gen = _generated_at(mode)
    meta, t = _load(mode, gen, mtime)
    ref = reference_date(meta)
    cfg = load_weights()
    cards, scores = _score(
        mode, meta.get("generated_at"), mtime, ref, json.dumps(cfg, sort_keys=True), t
    )
    return Data(mode, meta, ref, t, cards, scores, cfg)


@st.cache_resource(show_spinner=False, max_entries=2)
def _score(
    mode: str,
    generated_at: str | None,
    private_mtime: float,
    ref: date,
    weights_key: str,
    _t: dict[str, pl.DataFrame],
) -> tuple[dict[str, ScoreCard], pl.DataFrame]:
    cards = score_all(_t, ref)
    return cards, score_frame(cards).join(_t["company"], on="org_nr", how="left")
