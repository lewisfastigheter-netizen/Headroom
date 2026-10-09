"""ESMA FIRDS: reference data for debt instruments.

File index (Solr): https://registers.esma.europa.eu/solr/esma_registers_firds_files/select
Full files: FULINS_D_<yyyymmdd>_<n>of<m>.zip, published weekly, one record per
instrument per trading venue, ISO 20022 auth.017 XML.

The files are large, so they are streamed: each zip is downloaded to the cache,
the XML member is parsed with lxml.iterparse without extracting it, and parsed
elements are cleared as we go.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from lxml import etree

from headroom.config import CACHE_DIR
from headroom.http import Fetcher

log = logging.getLogger(__name__)

INDEX = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"

# CFI categories kept: DB bonds, DT medium-term notes, DC convertibles.
# Money-market (DY), structured (DE), ABS/MBS (DA, DG) and covered bonds are left out.
KEEP_CFI = ("DB", "DT", "DC")

# Fourth CFI character for bonds: guarantee or ranking.
CFI_SECURED = {"S": True, "U": False, "P": False, "N": False, "O": False, "Q": False, "J": False}


def latest_full_files(
    f: Fetcher, file_type: str = "FULINS", asset: str = "D", as_of: date | None = None
) -> list[dict]:
    """The complete set of the most recent full files for one asset class."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=21)
    params = {
        "q": "*",
        "fq": [
            f"file_type:{file_type}",
            f"publication_date:[{start.isoformat()}T00:00:00Z TO {as_of.isoformat()}T23:59:59Z]",
        ],
        "wt": "json",
        "rows": "500",
        "fl": "file_name,publication_date,download_link,checksum",
    }
    docs = f.get(INDEX, params=params, ttl=timedelta(hours=6)).json()["response"]["docs"]
    docs = [d for d in docs if d["file_name"].startswith(f"{file_type}_{asset}_")]
    if not docs:
        return []
    latest = max(d["publication_date"] for d in docs)
    return sorted(
        (d for d in docs if d["publication_date"] == latest), key=lambda d: d["file_name"]
    )


def _t(el: etree._Element | None, path: str) -> str | None:
    """Text at a slash-separated path of local names, ignoring namespaces."""
    if el is None:
        return None
    node = el
    for part in path.split("/"):
        nxt = None
        for child in node:
            if isinstance(child.tag, str) and etree.QName(child).localname == part:
                nxt = child
                break
        if nxt is None:
            return None
        node = nxt
    return (node.text or "").strip() or None


def _find(el: etree._Element, path: str) -> etree._Element | None:
    node = el
    for part in path.split("/"):
        node = next(
            (c for c in node if isinstance(c.tag, str) and etree.QName(c).localname == part), None
        )
        if node is None:
            return None
    return node


def _d(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


BENCHMARKS = {
    "STBO": "STIBOR",
    "STIBOR": "STIBOR",
    "NIBO": "NIBOR",
    "NIBOR": "NIBOR",
    "EURI": "EURIBOR",
    "EURIBOR": "EURIBOR",
    "CIBO": "CIBOR",
    "SOFR": "SOFR",
    "ESTR": "€STR",
    "SWESTR": "SWESTR",
}


def normalise_benchmark(raw: str | None) -> str | None:
    """'STBO', 'STIBOR - STBO' and 'STIBOR' all become 'STIBOR'."""
    if not raw:
        return None
    for token in raw.replace("-", " ").upper().split():
        if token in BENCHMARKS:
            return BENCHMARKS[token]
    return raw.strip()


def parse_record(ref: etree._Element) -> dict | None:
    cfi = _t(ref, "FinInstrmGnlAttrbts/ClssfctnTp") or ""
    if not cfi.startswith(KEEP_CFI):
        return None
    debt = _find(ref, "DebtInstrmAttrbts")
    nominal_el = _find(debt, "TtlIssdNmnlAmt") if debt is not None else None
    rate = _find(debt, "IntrstRate") if debt is not None else None
    fixed = _t(rate, "Fxd") if rate is not None else None
    flt = _find(rate, "Fltg") if rate is not None else None
    benchmark = None
    term = None
    spread = None
    if flt is not None:
        benchmark = normalise_benchmark(_t(flt, "RefRate/Indx") or _t(flt, "RefRate/Nm"))
        unit, val = _t(flt, "Term/Unit"), _t(flt, "Term/Val")
        term = f"{val}{unit[0]}" if unit and val else None  # e.g. 3M
        sp = _t(flt, "BsisPtSprd")
        spread = float(sp) if sp else None
    return {
        "isin": _t(ref, "FinInstrmGnlAttrbts/Id"),
        "full_name": _t(ref, "FinInstrmGnlAttrbts/FullNm"),
        "cfi": cfi,
        "currency": _t(ref, "FinInstrmGnlAttrbts/NtnlCcy"),
        "issuer_lei": _t(ref, "Issr"),
        "venue": _t(ref, "TradgVnRltdAttrbts/Id"),
        "first_trade": _d(_t(ref, "TradgVnRltdAttrbts/FrstTradDt")),
        "termination": _d(_t(ref, "TradgVnRltdAttrbts/TermntnDt")),
        "nominal": float(nominal_el.text) if nominal_el is not None and nominal_el.text else None,
        "nominal_ccy": nominal_el.get("Ccy") if nominal_el is not None else None,
        "maturity": _d(_t(debt, "MtrtyDt")) if debt is not None else None,
        "coupon_type": "fixed" if fixed is not None else "floating" if flt is not None else None,
        "coupon_pct": float(fixed) if fixed else None,
        "benchmark": benchmark,
        "benchmark_term": term,
        "margin_bp": spread,
        "authority": _t(ref, "TechAttrbts/RlvntCmptntAuthrty"),
    }


def iter_records(zip_path: Path, keep: Callable[[dict], bool] | None = None) -> Iterator[dict]:
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        with z.open(member) as fh:
            for _, el in etree.iterparse(fh, events=("end",), tag="{*}RefData", huge_tree=True):
                rec = parse_record(el)
                el.clear(keep_tail=False)
                while el.getprevious() is not None:
                    del el.getparent()[0]
                if rec and rec["isin"] and (keep is None or keep(rec)):
                    yield rec


def nordic_candidate(as_of: date) -> Callable[[dict], bool]:
    """Pre-filter before the issuer lookup: outstanding bonds that could be Swedish.

    Swedish issuers use SE ISINs for domestic bonds and XS for euro-market
    programmes, sometimes NO ISINs for Nordic Trustee bonds under Norwegian law.
    The issuer's country is confirmed afterwards via GLEIF.
    """

    def keep(r: dict) -> bool:
        if r["maturity"] is not None and r["maturity"] < as_of:
            return False
        prefix = r["isin"][:2]
        if prefix in ("SE", "NO", "FI", "DK"):
            return True
        return prefix == "XS" and r["currency"] in ("SEK", "EUR", "NOK")

    return keep


def load_universe(
    f: Fetcher, as_of: date | None = None, workdir: Path | None = None, keep_zips: bool = False
) -> tuple[pl.DataFrame, dict]:
    """Download and parse the latest FULINS_D set. Returns venue-level rows and file metadata."""
    as_of = as_of or date.today()
    workdir = workdir or CACHE_DIR / "firds"
    files = latest_full_files(f, as_of=as_of)
    if not files:
        raise RuntimeError("No FULINS_D files found in the FIRDS index")
    keep = nordic_candidate(as_of)
    rows: list[dict] = []
    for d in files:
        path = workdir / d["file_name"]
        log.info("FIRDS: %s", d["file_name"])
        f.download(d["download_link"], path, check_robots=False)  # file host has no robots.txt
        for rec in iter_records(path, keep):
            rec["source_file"] = d["file_name"]
            rows.append(rec)
        if not keep_zips:
            path.unlink(missing_ok=True)
    meta = {
        "publication_date": files[0]["publication_date"][:10],
        "files": [d["file_name"] for d in files],
    }
    return pl.DataFrame(rows, infer_schema_length=None), meta


def dedupe(venue_rows: pl.DataFrame) -> pl.DataFrame:
    """One row per ISIN. Venue-specific fields are aggregated."""
    if venue_rows.is_empty():
        return venue_rows
    static = [
        "full_name",
        "cfi",
        "currency",
        "issuer_lei",
        "nominal",
        "nominal_ccy",
        "maturity",
        "coupon_type",
        "coupon_pct",
        "benchmark",
        "benchmark_term",
        "margin_bp",
        "authority",
        "source_file",
    ]
    return (
        venue_rows.sort(["isin", "first_trade"], nulls_last=True)
        .group_by("isin", maintain_order=True)
        .agg(
            *[pl.col(c).drop_nulls().first().alias(c) for c in static if c in venue_rows.columns],
            pl.col("venue").drop_nulls().unique().sort().alias("venues"),
            pl.col("first_trade").min().alias("first_trade"),
            pl.col("termination").max().alias("last_termination"),
        )
        .with_columns(
            pl.col("cfi")
            .str.slice(3, 1)
            .replace_strict(CFI_SECURED, default=None)
            .alias("secured"),
        )
    )


# --------------------------------------------------------------------------- equities

# Swedish primary venues for shares, checked against the FULINS_E files of
# 3 October 2026 (number of SE-ISIN shares in brackets): Nasdaq Stockholm XSTO
# (383), Nasdaq First North Sweden SSME (313), Spotlight XSAT (133),
# NGM Nordic SME NSME (87), NGM Main Regulated XNGM (10). Venues such as FRAB
# or SGMU only admit shares already listed elsewhere, so they are left out.
SWEDISH_EQUITY_VENUES = {"XSTO", "SSME", "XSAT", "NSME", "XNGM"}


def parse_equity(ref: etree._Element) -> dict | None:
    cfi = _t(ref, "FinInstrmGnlAttrbts/ClssfctnTp") or ""
    if not cfi.startswith("ES"):
        return None
    return {
        "isin": _t(ref, "FinInstrmGnlAttrbts/Id"),
        "short_name": _t(ref, "FinInstrmGnlAttrbts/ShrtNm"),
        "issuer_lei": _t(ref, "Issr"),
        "venue": _t(ref, "TradgVnRltdAttrbts/Id"),
        "termination": _d(_t(ref, "TradgVnRltdAttrbts/TermntnDt")),
    }


def iter_equities(zip_path: Path, keep: Callable[[dict], bool] | None = None) -> Iterator[dict]:
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        with z.open(member) as fh:
            for _, el in etree.iterparse(fh, events=("end",), tag="{*}RefData", huge_tree=True):
                rec = parse_equity(el)
                el.clear(keep_tail=False)
                while el.getprevious() is not None:
                    del el.getparent()[0]
                if rec and rec["isin"] and (keep is None or keep(rec)):
                    yield rec


def listed_shares(
    f: Fetcher, as_of: date | None = None, workdir: Path | None = None, keep_zips: bool = False
) -> tuple[pl.DataFrame, dict]:
    """Shares admitted to trading on a Swedish venue and not terminated."""
    as_of = as_of or date.today()
    workdir = workdir or CACHE_DIR / "firds"
    files = latest_full_files(f, asset="E", as_of=as_of)
    if not files:
        raise RuntimeError("No FULINS_E files found in the FIRDS index")

    def keep(r: dict) -> bool:
        return r["venue"] in SWEDISH_EQUITY_VENUES and (
            r["termination"] is None or r["termination"] >= as_of
        )

    rows: list[dict] = []
    for d in files:
        path = workdir / d["file_name"]
        f.download(d["download_link"], path, check_robots=False)
        rows.extend(iter_equities(path, keep))
        if not keep_zips:
            path.unlink(missing_ok=True)
    meta = {
        "publication_date": files[0]["publication_date"][:10],
        "files": [d["file_name"] for d in files],
    }
    return pl.DataFrame(rows, infer_schema_length=None), meta
