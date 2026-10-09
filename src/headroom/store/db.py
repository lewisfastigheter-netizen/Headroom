"""DuckDB over Parquet snapshots.

Pipelines write one Parquet file per table into `data/snapshots/<mode>/`.
The app opens an in-memory DuckDB connection with a view per table, which keeps
deployment simple (no database file to ship) and lets the weekly job commit
plain Parquet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from headroom.config import SNAPSHOT_DIR

TABLES: dict[str, str] = {
    "company": """
        org_nr VARCHAR PRIMARY KEY, lei VARCHAR, name VARCHAR, tier VARCHAR,
        listed_ticker VARCHAR, sni VARCHAR, segment_mix VARCHAR, region_mix VARCHAR,
        size DOUBLE, universe_rule VARCHAR, source_url VARCHAR, as_of DATE""",
    "bond": """
        isin VARCHAR PRIMARY KEY, org_nr VARCHAR, full_name VARCHAR, nominal DOUBLE,
        currency VARCHAR, nominal_sek DOUBLE,
        issue_date DATE, maturity DATE, coupon_type VARCHAR, benchmark VARCHAR,
        margin_bp DOUBLE, coupon_pct DOUBLE, secured BOOLEAN, venue VARCHAR,
        trustee VARCHAR, source_url VARCHAR, as_of DATE""",
    "covenant": """
        isin VARCHAR, test VARCHAR, threshold DOUBLE, kind VARCHAR,
        source_url VARCHAR, page INTEGER, confidence DOUBLE""",
    "financials": """
        org_nr VARCHAR, period_end DATE, period_type VARCHAR, property_value DOUBLE,
        gross_debt DOUBLE, net_debt DOUBLE, ltv DOUBLE, icr DOUBLE, ebitda DOUBLE,
        interest_expense DOUBLE, avg_rate DOUBLE, fixed_share DOUBLE,
        fixed_period_years DOUBLE, equity_ratio DOUBLE, cash DOUBLE,
        undrawn_facilities DOUBLE, debt_due_12m DOUBLE, debt_due_24m DOUBLE,
        source_url VARCHAR, page INTEGER, confidence DOUBLE""",
    # Field-level provenance for extracted figures: one row per (company, period, field).
    "provenance": """
        table_name VARCHAR, org_nr VARCHAR, period_end DATE, field VARCHAR,
        source_url VARCHAR, page INTEGER, confidence DOUBLE, method VARCHAR""",
    "event": """
        org_nr VARCHAR, date DATE, type VARCHAR, severity INTEGER, title VARCHAR,
        source_url VARCHAR, source_name VARCHAR""",
    "price": """
        ticker VARCHAR, date DATE, close DOUBLE, nav_per_share DOUBLE, source_url VARCHAR""",
    "rate": """series VARCHAR, date DATE, value DOUBLE, source_url VARCHAR""",
}


@dataclass
class Snapshot:
    mode: str
    path: Path
    meta: dict[str, Any]
    con: duckdb.DuckDBPyConnection

    @property
    def fictional(self) -> bool:
        return bool(self.meta.get("fictional", False))

    def df(self, sql: str, params: list[Any] | None = None) -> pl.DataFrame:
        return self.con.execute(sql, params or []).pl()

    def table(self, name: str) -> pl.DataFrame:
        return self.df(f"SELECT * FROM {name}")

    def as_of(self, name: str) -> str | None:
        return self.meta.get("as_of", {}).get(name)


def snapshot_dir(mode: str) -> Path:
    return SNAPSHOT_DIR / mode


def open_snapshot(mode: str) -> Snapshot:
    path = snapshot_dir(mode)
    meta_path = path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    con = duckdb.connect(":memory:")
    for name, ddl in TABLES.items():
        file = path / f"{name}.parquet"
        if file.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{file.as_posix()}')")
        else:
            # Empty table with the right columns, so pages degrade instead of failing.
            cols = ddl.replace("PRIMARY KEY", "")
            con.execute(f"CREATE TABLE {name} ({cols})")
    return Snapshot(mode=mode, path=path, meta=meta, con=con)


def write_snapshot(
    mode: str,
    tables: dict[str, pl.DataFrame],
    *,
    fictional: bool,
    as_of: dict[str, str] | None = None,
    notes: str | None = None,
    extra: dict | None = None,
) -> Path:
    """Validate tables against the DDL via DuckDB, then write Parquet + meta.json."""
    path = snapshot_dir(mode)
    path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    for name, frame in tables.items():
        if name not in TABLES:
            raise KeyError(f"unknown table {name}")
        con.execute(f"CREATE TABLE {name} ({TABLES[name]})")
        con.register("frame", frame.to_arrow())
        cols = ", ".join(frame.columns)
        con.execute(f"INSERT INTO {name} ({cols}) SELECT {cols} FROM frame")
        con.unregister("frame")
        con.execute(f"COPY {name} TO '{(path / f'{name}.parquet').as_posix()}' (FORMAT parquet)")
    meta = {
        "mode": mode,
        "fictional": fictional,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": as_of or {},
        "notes": notes,
        **(extra or {}),
    }
    (path / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str), "utf-8"
    )
    return path
