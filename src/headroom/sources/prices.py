"""Share prices for listed companies via yfinance (Yahoo Finance, .ST tickers).

Tickers come from MFN's company record (e.g. 'XSTO:SAGA B' -> 'SAGA-B.ST'), so
the share is tied to the company through its organisation number, not a name
match. yfinance is an unofficial interface: prices are labelled with their
source and date, and a failed download leaves the market component unscored.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

log = logging.getLogger(__name__)

SCHEMA = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "close": pl.Float64,
    "nav_per_share": pl.Float64,
    "source_url": pl.Utf8,
}


def history(tickers: list[str], start: date, end: date | None = None) -> pl.DataFrame:
    import yfinance as yf

    end = end or date.today()
    rows: list[dict] = []
    for t in sorted(set(tickers)):
        try:
            h = yf.Ticker(t).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                interval="1wk",
                auto_adjust=False,
            )
        except Exception as e:  # network or symbol errors: skip, the component stays unscored
            log.warning("prices: %s failed: %s", t, e)
            continue
        if h is None or h.empty:
            log.warning("prices: no data for %s", t)
            continue
        for idx, r in h.iterrows():
            rows.append(
                {
                    "ticker": t,
                    "date": idx.date(),
                    "close": float(r["Close"]),
                    "nav_per_share": None,
                    "source_url": f"https://finance.yahoo.com/quote/{t}/history",
                }
            )
    return pl.DataFrame(rows, schema=SCHEMA)


def attach_nav(prices: pl.DataFrame, nav: pl.DataFrame) -> pl.DataFrame:
    """As-of join: each price row gets the latest NAV per share reported before that date.

    `nav` has columns ticker, period_end, report_date, nav_per_share. A NAV only
    becomes known when the report is published, so the join is on report_date.
    """
    if prices.is_empty() or nav.is_empty():
        return prices
    n = nav.select(
        "ticker", pl.col("report_date").alias("date"), pl.col("nav_per_share").alias("nav")
    ).sort("ticker", "date")
    out = (
        prices.drop("nav_per_share")
        .sort("ticker", "date")
        .join_asof(n, on="date", by="ticker", strategy="backward")
    )
    return out.rename({"nav": "nav_per_share"}).select(list(SCHEMA))
