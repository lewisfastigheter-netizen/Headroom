"""Statistics Sweden (SCB) PxWebApi 2.0.

Base https://statistikdatabasen.scb.se/api/v2/ (CC0). The API allows 150,000 data cells
per call and 30 calls per 10 seconds (read from /config at start). This client:

- splits a selection that is over the cell limit into several calls (by period first),
- keeps to the rate limit through the shared `Fetcher` (host interval, backoff on 429),
- caches each table selection as Parquet and skips the download when the table's
  `updated` timestamp has not changed,
- turns json-stat2 into a long Polars frame, with SCB's dots ('..' not available,
  '.' not applicable) as null, never as zero.

RegSO and DeSO come in two versions. Codes without a suffix (0114R001, 0114A0010) are
the 2020 version (built on DeSO 2018) and hold statistics up to 2023; codes with the
suffix `_RegSO2025` / `_DeSO2025` hold statistics from 2024. Some tables return 0, not
'..', for years outside a version's range, so those rows are dropped by version and
year, not by value. The suffix is removed and the version kept in its own column.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from headroom.config import CACHE_DIR
from headroom.http import Fetcher

log = logging.getLogger(__name__)

BASE = "https://statistikdatabasen.scb.se/api/v2"
TABLE_URL = "https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/{}/"  # human-readable link
MISSING = {"..", ".", "...", "-"}
DEFAULT_MAX_CELLS = 150_000

_RE_REGSO20 = re.compile(r"^\d{4}R\d{3}$")
_RE_DESO18 = re.compile(r"^\d{4}[ABC]\d{4}$")


def table_url(table: str) -> str:
    """Link shown in the app next to a figure: the table's page in Statistikdatabasen."""
    return f"{BASE}/tables/{table}?lang=sv"


def split_version(code: str) -> tuple[str, str | None]:
    """'0114R001_RegSO2025' -> ('0114R001', '2025'); '0114R001' -> ('0114R001', '2020');
    other region codes -> (code, None)."""
    if code.endswith("_RegSO2025") or code.endswith("_DeSO2025"):
        return code.split("_", 1)[0], "2025"
    if _RE_REGSO20.match(code) or _RE_DESO18.match(code):
        return code, "2020"
    return code, None


def version_valid(version: str | None, year: int | None) -> bool:
    """Statistics on the 2020 version run to 2023; on the 2025 version from 2024."""
    if version is None or year is None:
        return True
    return year <= 2023 if version == "2020" else year >= 2024


def _year(period: str | None) -> int | None:
    m = re.match(r"(\d{4})", period or "")
    return int(m.group(1)) if m else None


@dataclass
class Table:
    """Metadata for one table: dimension ids, codes per dimension, labels, `updated`."""

    id: str
    label: str
    updated: str
    dims: list[str]
    codes: dict[str, list[str]]
    labels: dict[str, dict[str, str]]
    codelists: dict[str, list[str]] = field(default_factory=dict)
    time_dim: str | None = None


def parse_jsonstat(js: dict) -> tuple[pl.DataFrame, dict[str, dict[str, str]]]:
    """json-stat2 -> long frame with one column per dimension (codes) and `value`.

    Missing markers in `status` ('..', '.') and null values become null. Returns the frame
    and the label dict {dimension: {code: label}}.
    """
    dims: list[str] = js["id"]
    sizes: list[int] = js["size"]
    cats = {d: js["dimension"][d]["category"] for d in dims}
    order: dict[str, list[str]] = {}
    for d in dims:
        idx = cats[d].get("index")
        if isinstance(idx, dict):
            order[d] = [c for c, _ in sorted(idx.items(), key=lambda kv: kv[1])]
        elif isinstance(idx, list):
            order[d] = list(idx)
        else:  # single category given only by label
            order[d] = list(cats[d].get("label", {}))
    labels = {d: dict(cats[d].get("label", {})) for d in dims}
    n = math.prod(sizes) if sizes else 0
    raw = js.get("value", [])
    if isinstance(raw, dict):  # sparse form: {"index": value}
        values: list[Any] = [None] * n
        for k, v in raw.items():
            values[int(k)] = v
    else:
        values = list(raw)
    status = js.get("status") or {}
    if isinstance(status, list):
        status = {str(i): s for i, s in enumerate(status) if s}
    for k, s in status.items():
        if s in MISSING:
            values[int(k)] = None
    cols: dict[str, list] = {d: [] for d in dims}
    for combo in itertools.product(*(order[d] for d in dims)):
        for d, c in zip(dims, combo, strict=True):
            cols[d].append(c)
    frame = pl.DataFrame(
        {
            **cols,
            "value": pl.Series([None if v is None else float(v) for v in values], dtype=pl.Float64),
        }
    )
    return frame, labels


class SCB:
    def __init__(self, f: Fetcher, cache_dir: Path | None = None):
        self.f = f
        self.cache = (cache_dir or CACHE_DIR) / "scb"
        self.cache.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, Table] = {}
        self._codelists: dict[str, list[str]] = {}
        self.max_cells = DEFAULT_MAX_CELLS
        self._config_read = False

    # ------------------------------------------------------------------ http

    def _get_json(self, path: str, params: dict | None = None) -> Any:
        r = self.f._get(f"{BASE}{path}", params, None)
        r.raise_for_status()
        return r.json()

    def config(self) -> dict:
        cfg = self._get_json("/config")
        self.max_cells = int(cfg.get("maxDataCells") or DEFAULT_MAX_CELLS)
        self._config_read = True
        return cfg

    def metadata(self, table: str) -> Table:
        if table in self._meta:
            return self._meta[table]
        js = self._get_json(f"/tables/{table}/metadata", {"lang": "sv"})
        dims = js["id"]
        codes, labels, cls = {}, {}, {}
        time_dim = None
        for d in dims:
            cat = js["dimension"][d]["category"]
            idx = cat.get("index", {})
            codes[d] = (
                [c for c, _ in sorted(idx.items(), key=lambda kv: kv[1])]
                if isinstance(idx, dict)
                else list(idx)
            )
            labels[d] = dict(cat.get("label", {}))
            ext = js["dimension"][d].get("extension") or {}
            cls[d] = [c["id"] for c in ext.get("codelists") or []]
            if d.lower() == "tid" or (js.get("role", {}).get("time") or [None])[0] == d:
                time_dim = d
        t = Table(
            table, js.get("label", ""), js.get("updated", ""), dims, codes, labels, cls, time_dim
        )
        self._meta[table] = t
        return t

    def codelist(self, codelist_id: str) -> list[dict]:
        """Values of a codelist; for aggregations each value has `valueMap` (members)."""
        js = self._get_json(f"/codelists/{codelist_id}", {"lang": "sv"})
        return js.get("values", [])

    # ------------------------------------------------------------------ data

    def _size(self, t: Table, dim: str, sel: list[str] | str, codelist: str | None) -> int:
        if sel != "*":
            return len(sel)
        if codelist:
            if codelist not in self._codelists:
                self._codelists[codelist] = [v["code"] for v in self.codelist(codelist)]
            return len(self._codelists[codelist])
        return len(t.codes[dim])

    def _chunks(
        self, t: Table, selection: dict[str, list[str] | str], codelists: dict[str, str]
    ) -> list[dict[str, list[str] | str]]:
        """Split the selection until each part is under the cell limit. Splits the time
        dimension first, then the longest explicit list."""
        sizes = {d: self._size(t, d, selection[d], codelists.get(d)) for d in t.dims}
        total = math.prod(sizes.values())
        if total <= self.max_cells:
            return [selection]
        order = sorted(
            (d for d in t.dims if selection[d] != "*" or d == t.time_dim),
            key=lambda d: (d != t.time_dim, -sizes[d]),
        )
        for d in order:
            values = t.codes[d] if selection[d] == "*" else list(selection[d])
            if len(values) < 2:
                continue
            rest = total // sizes[d]
            per = max(1, self.max_cells // max(rest, 1))
            if rest > self.max_cells:  # this dimension alone cannot fix it: split fully
                per = 1
            parts = [values[i : i + per] for i in range(0, len(values), per)]
            out = []
            for p in parts:
                out.extend(self._chunks(t, {**selection, d: p}, codelists))
            return out
        raise ValueError(f"{t.id}: selection of {total} cells cannot be split under the limit")

    def _fetch(self, t: Table, sel: dict[str, list[str] | str], codelists: dict[str, str]) -> dict:
        """POST /tables/{id}/data with a selection body (long code lists do not fit in
        a URL)."""
        selection = []
        for d in t.dims:
            v = sel[d]
            item: dict[str, Any] = {"variableCode": d, "valueCodes": ["*"] if v == "*" else list(v)}
            if d in codelists:
                item["codelist"] = codelists[d]
            selection.append(item)
        r = self.f.post_json(
            f"{BASE}/tables/{t.id}/data",
            {"selection": selection},
            params={"lang": "sv", "outputFormat": "json-stat2"},
        )
        r.raise_for_status()
        return r.json()

    def data(
        self,
        table: str,
        selection: dict[str, list[str] | str],
        codelists: dict[str, str] | None = None,
        *,
        use_cache: bool = True,
    ) -> pl.DataFrame:
        """Long frame for a selection. Dimensions not named are taken in full ('*').

        Columns: one per dimension (codes), `value`; plus `region` and `regso_version`
        when the table has a region dimension, and `period` for the time dimension.
        """
        if not self._config_read:
            try:
                self.config()
            except Exception as e:  # keep the documented defaults
                log.warning("SCB /config unavailable (%s); using defaults", e)
                self._config_read = True
        t = self.metadata(table)
        codelists = codelists or {}
        sel = {d: selection.get(d, "*") for d in t.dims}
        key = hashlib.sha256(
            json.dumps([table, sel, codelists], sort_keys=True).encode()
        ).hexdigest()[:20]
        pq, meta_p = self.cache / f"{table}_{key}.parquet", self.cache / f"{table}_{key}.json"
        fresh = (
            use_cache
            and pq.exists()
            and meta_p.exists()
            and json.loads(meta_p.read_text()).get("updated") == t.updated
        )
        if fresh:
            log.info("SCB %s unchanged since %s, using cache", table, t.updated)
            return pl.read_parquet(pq)
        frames = []
        for part in self._chunks(t, sel, codelists):
            frame, _ = parse_jsonstat(self._fetch(t, part, codelists))
            frames.append(frame)
        out = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
        out = self._tidy(t, out)
        out.write_parquet(pq)
        meta_p.write_text(json.dumps({"updated": t.updated, "table": table}))
        log.info("SCB %s: %d rows (updated %s)", table, out.height, t.updated)
        return out

    @staticmethod
    def _tidy(t: Table, frame: pl.DataFrame) -> pl.DataFrame:
        if frame.is_empty():
            return frame
        if t.time_dim and t.time_dim in frame.columns:
            frame = frame.rename({t.time_dim: "period"})
        reg = next((d for d in t.dims if d.lower() == "region"), None)
        if reg and reg in frame.columns:
            pairs = [split_version(c) for c in frame[reg].to_list()]
            frame = frame.with_columns(
                pl.Series("region", [p[0] for p in pairs], dtype=pl.Utf8),
                pl.Series("regso_version", [p[1] for p in pairs], dtype=pl.Utf8),
            )
            if reg != "region":
                frame = frame.drop(reg)
            if "period" in frame.columns:
                ok = [
                    version_valid(v, _year(p))
                    for v, p in zip(frame["regso_version"], frame["period"], strict=True)
                ]
                # outside the version's years: not a suppressed value, not a value at all
                frame = frame.filter(pl.Series(ok))
        return frame
