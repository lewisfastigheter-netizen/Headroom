"""Cached access to the active snapshot and its scores."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import polars as pl
import streamlit as st

from headroom.config import resolve_mode
from headroom.config import weights as load_weights
from headroom.model.scoring import ScoreCard, reference_date, score_all, score_frame
from headroom.store.db import TABLES, open_snapshot


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


@st.cache_data(show_spinner=False)
def _load(mode: str, generated_at: str | None) -> tuple[dict, dict[str, pl.DataFrame]]:
    snap = open_snapshot(mode)
    return snap.meta, {name: snap.table(name) for name in TABLES}


def available_modes() -> list[str]:
    from headroom.store.db import snapshot_dir

    modes = ["live"] if (snapshot_dir("live") / "bond.parquet").exists() else []
    return [*modes, "demo"]


def current_mode() -> str:
    mode = st.session_state.get("mode") or st.query_params.get("data") or resolve_mode()
    return mode if mode in available_modes() else available_modes()[0]


def get_data() -> Data:
    mode = current_mode()
    probe = open_snapshot(mode)
    meta, t = _load(mode, probe.meta.get("generated_at"))
    ref = reference_date(meta)
    cfg = load_weights()
    cards = _score(mode, meta.get("generated_at"), ref, json.dumps(cfg, sort_keys=True), t)
    scores = score_frame(cards).join(t["company"], on="org_nr", how="left")
    return Data(mode, meta, ref, t, cards, scores, cfg)


@st.cache_data(show_spinner=False)
def _score(
    mode: str, generated_at: str | None, ref: date, weights_key: str, _t: dict[str, pl.DataFrame]
) -> dict[str, ScoreCard]:
    return score_all(_t, ref)
