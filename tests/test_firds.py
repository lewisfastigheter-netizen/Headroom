import zipfile
from datetime import date
from pathlib import Path

import pytest

from headroom.sources.firds import dedupe, iter_records, nordic_candidate

FIXTURE = Path(__file__).parent / "fixtures" / "fulins_d_sample.xml"


@pytest.fixture
def zipped(tmp_path):
    z = tmp_path / "FULINS_D_20261003_01of01.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(FIXTURE, "FULINS_D_20261003_01of01.xml")
    return z


def test_parse_and_filter(zipped):
    rows = list(iter_records(zipped, nordic_candidate(date(2026, 10, 8))))
    isins = [r["isin"] for r in rows]
    assert isins == ["SE0000000001", "SE0000000001", "XS0000000002"]
    frn = rows[0]
    assert frn["coupon_type"] == "floating"
    assert frn["benchmark"] == "STIBOR" and frn["benchmark_term"] == "3M"
    assert frn["margin_bp"] == 650
    assert frn["nominal"] == 900_000_000 and frn["nominal_ccy"] == "SEK"
    assert frn["maturity"] == date(2026, 10, 30)
    assert rows[2]["coupon_type"] == "fixed" and rows[2]["coupon_pct"] == 2.5


def test_dedupe_one_row_per_isin(zipped):
    import polars as pl

    df = dedupe(pl.DataFrame(list(iter_records(zipped, nordic_candidate(date(2026, 10, 8))))))
    assert df.height == 2
    frn = df.filter(pl.col("isin") == "SE0000000001").row(0, named=True)
    assert frn["venues"] == ["FRAA", "XSTO"]
    assert frn["first_trade"] == date(2023, 10, 30)
    assert frn["secured"] is True  # CFI DBVSFR: fourth character S


def test_normalise_benchmark():
    from headroom.sources.firds import normalise_benchmark

    assert normalise_benchmark("STIBOR - STBO") == "STIBOR"
    assert normalise_benchmark("NIBO") == "NIBOR"
    assert normalise_benchmark(None) is None
