"""Private property companies found at Bolagsverket, kept outside the snapshot.

The daily private-company job appends to `data/private_found.jsonl` (one company per
line: company row, financials row, events), and the app merges it into the live data
on load. Keeping it out of the snapshot files means the daily job and the weekly
refresh never write the same file, so their commits never conflict.
"""

from __future__ import annotations

import json
from datetime import date

import polars as pl

from headroom.config import DATA_DIR

PRIVATE_FOUND = DATA_DIR / "private_found.jsonl"
PRIVATE_CANDIDATES = DATA_DIR / "private_candidates.parquet"
PRIVATE_LEDGER = DATA_DIR / "private_checked.csv"

STATUS_LABEL = {
    "kept": "In Headroom (SEK 20m+ of property)",
    "below_threshold": "Property company, under SEK 20m",
    "not_real_estate": "Not a property company (SNI code)",
    "no_digital_report": "No digitally filed annual report",
    "no_ixbrl": "Annual report not machine-readable",
}


def progress() -> dict:
    """How far the private-company scan has come: candidates, checked, per result."""
    total = (
        pl.scan_parquet(PRIVATE_CANDIDATES).select(pl.len()).collect().item()
        if (PRIVATE_CANDIDATES.exists())
        else 0
    )
    if not PRIVATE_LEDGER.exists():
        return {"candidates": total, "checked": 0, "by_status": {}, "last_checked": None}
    led = pl.read_csv(PRIVATE_LEDGER, infer_schema_length=0)
    by = {r["status"]: r["count"] for r in led["status"].value_counts().to_dicts()}
    return {
        "candidates": total,
        "checked": led.height,
        "by_status": by,
        "last_checked": led["checked"].max(),
    }


def progress_markdown() -> str:
    p = progress()
    pct = p["checked"] / p["candidates"] * 100 if p["candidates"] else 0
    lines = [
        "### Private-company scan",
        f"Checked **{p['checked']:,}** of **{p['candidates']:,}** candidates ({pct:.1f}%). "
        f"Last checked {p['last_checked'] or '–'}.",
        "",
        "| Result | Companies |",
        "|---|---:|",
    ]
    for k, label in STATUS_LABEL.items():
        lines.append(f"| {label} | {p['by_status'].get(k, 0):,} |")
    return "\n".join(lines)


def load_found() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if PRIVATE_FOUND.exists():
        for line in PRIVATE_FOUND.read_text("utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["company"]["org_nr"]] = rec
    return out


def save_found(found: dict[str, dict]) -> None:
    lines = [json.dumps(found[k], ensure_ascii=False, default=str) for k in sorted(found)]
    PRIVATE_FOUND.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")


def _dates(d: dict, *keys: str) -> dict:
    return {**d, **{k: date.fromisoformat(str(d[k])[:10]) for k in keys if d.get(k)}}


def found_tables(exclude: set[str]) -> dict[str, pl.DataFrame | None]:
    """company, financials and event rows for every kept private company."""
    recs = [r for org, r in load_found().items() if org not in exclude]
    companies = [_dates(r["company"], "as_of") for r in recs]
    fins = [_dates(r["financials"], "period_end") for r in recs]
    evs = [_dates(e, "date") for r in recs for e in r.get("events") or []]
    return {
        "company": pl.DataFrame(companies, infer_schema_length=None) if companies else None,
        "financials": pl.DataFrame(fins, infer_schema_length=None) if fins else None,
        "event": pl.DataFrame(evs, infer_schema_length=None) if evs else None,
    }


def merge(tables: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """Live tables plus the private companies, columns aligned to the snapshot's."""
    extra = found_tables(set(tables["company"]["org_nr"].to_list()))
    out = dict(tables)
    for name, frame in extra.items():
        if frame is None or not frame.height:
            continue
        base = out[name]
        cols = [c for c in base.columns if c in frame.columns]
        frame = frame.select(cols).cast({c: base.schema[c] for c in cols}, strict=False)
        out[name] = pl.concat([base, frame], how="diagonal_relaxed").select(base.columns)
    return out
