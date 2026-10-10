"""Sveriges Riksbank: policy rate, SWESTR and the 3-month treasury bill.

SWEA API (series observations): https://api.riksbank.se/swea/v1
SWESTR API (overnight reference rate): https://api.riksbank.se/swestr/v1

STIBOR is not available here: the SWEA STIBOR series were closed in July 2020
for licensing reasons. SWESTR (overnight) and the 3-month treasury bill are the
free public proxies for the floating-rate base that Swedish property bonds pay.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from headroom.http import Fetcher

SWEA = "https://api.riksbank.se/swea/v1"
SWESTR = "https://api.riksbank.se/swestr/v1"

SWEA_SERIES = {
    "policy_rate": "SECBREPOEFF",  # Policy rate
    "tbill_3m": "SETB3MBENCH",  # SE TB 3 Months, benchmark
    # Fixed-rate references for underwriting: government bonds and covered (mortgage)
    # bonds. The 5-year mortgage bond is the closest free proxy for a 5-year swap.
    "gvb_2y": "SEGVB2YC",
    "gvb_5y": "SEGVB5YC",
    "gvb_10y": "SEGVB10YC",
    "mb_2y": "SEMB2YCACOMB",
    "mb_5y": "SEMB5YCACOMB",
}


def swea_observations(f: Fetcher, series_id: str, start: date, end: date) -> list[dict]:
    url = f"{SWEA}/Observations/{series_id}/{start.isoformat()}/{end.isoformat()}"
    return f.get(url, ttl=timedelta(hours=12)).json()


def swestr_observations(f: Fetcher, start: date, end: date) -> list[dict]:
    url = f"{SWESTR}/all/SWESTR"
    params = {"fromDate": start.isoformat(), "toDate": end.isoformat()}
    return f.get(url, params=params, ttl=timedelta(hours=12)).json()


def fetch_rates(f: Fetcher, start: date, end: date | None = None) -> pl.DataFrame:
    """Daily observations as rows of (series, date, value in per cent, source_url)."""
    end = end or date.today()
    rows: list[dict] = []
    for name, sid in SWEA_SERIES.items():
        for o in swea_observations(f, sid, start, end):
            rows.append(
                {
                    "series": name,
                    "date": date.fromisoformat(o["date"]),
                    "value": float(o["value"]),
                    "source_url": f"{SWEA}/Observations/{sid}",
                }
            )
    for o in swestr_observations(f, start, end):
        rows.append(
            {
                "series": "swestr",
                "date": date.fromisoformat(o["date"]),
                "value": float(o["rate"]),
                "source_url": f"{SWESTR}/all/SWESTR",
            }
        )
    return pl.DataFrame(
        rows,
        schema={"series": pl.Utf8, "date": pl.Date, "value": pl.Float64, "source_url": pl.Utf8},
    )


FX_SERIES = {
    "EUR": "SEKEURPMI",
    "NOK": "SEKNOKPMI",
    "USD": "SEKUSDPMI",
    "DKK": "SEKDKKPMI",
    "GBP": "SEKGBPPMI",
    "CHF": "SEKCHFPMI",
}


def latest_fx(f: Fetcher, currencies: set[str]) -> dict[str, dict]:
    """SEK per unit of currency, Riksbank mid rate. SEK maps to 1."""
    out: dict[str, dict] = {"SEK": {"rate": 1.0, "date": None, "source_url": None}}
    for ccy in sorted(currencies - {"SEK"}):
        sid = FX_SERIES.get(ccy)
        if not sid:
            continue
        o = f.get(f"{SWEA}/Observations/Latest/{sid}", ttl=timedelta(hours=12)).json()
        out[ccy] = {
            "rate": float(o["value"]),
            "date": o["date"],
            "source_url": f"{SWEA}/Observations/{sid}",
        }
    return out
