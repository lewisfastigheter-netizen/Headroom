"""Bank lending rates to non-financial companies (SCB financial market statistics).

TAB5780: interest rates on new and renegotiated loans from MFIs to non-financial
companies, by original fixed-rate period, monthly. Not property-specific, but it is the
public measure of what Swedish companies pay their banks, and it moves with the margins
property owners are offered. Stored as rows of the rate table, in per cent, dated at the
end of the month.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date

import polars as pl

from headroom.sources.scb import SCB, table_url

log = logging.getLogger(__name__)

TABLE = "TAB5780"
FIXATION = {
    "1": "bank_nfc_all",  # all new loans
    "1.1.1": "bank_nfc_floating",  # up to 3 months
    "1.1.2.2.2": "bank_nfc_3_5y",  # over 3 to 5 years
    "1.1.2.3": "bank_nfc_5y_plus",  # over 5 years
}


def _month_end(period: str) -> date:
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def fetch_lending_rates(scb: SCB, months: int = 48) -> pl.DataFrame:
    meta = scb.metadata(TABLE)
    periods = meta.codes[meta.time_dim][-months:]
    raw = scb.data(
        TABLE,
        {
            "Referenssektor": ["1.1"],
            "Motpartssektor": ["1"],
            "Avtal": ["0100"],
            "Rantebindningstid": list(FIXATION),
            "ContentsCode": "*",
            meta.time_dim: periods,
        },
    )
    fix_col = next(c for c in raw.columns if c.lower().startswith("rantebind"))
    out = raw.filter(pl.col("value").is_not_null()).select(
        pl.col(fix_col).replace_strict(FIXATION, default=None).alias("series"),
        pl.col("period").map_elements(_month_end, return_dtype=pl.Date).alias("date"),
        pl.col("value").cast(pl.Float64),
        pl.lit(table_url(TABLE)).alias("source_url"),
    )
    return out.filter(pl.col("series").is_not_null())
