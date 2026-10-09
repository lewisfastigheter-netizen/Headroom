"""Connector tests against recorded responses (no network)."""

import json
from datetime import date
from pathlib import Path

import httpx
import respx

from headroom.http import Fetcher
from headroom.model.universe import classify
from headroom.sources import gleif, riksbank

FIX = Path(__file__).parent / "fixtures"


def fetcher(tmp_path) -> Fetcher:
    f = Fetcher(cache_dir=tmp_path)
    f._robots = {"https://api.gleif.org": None, "https://api.riksbank.se": None}  # skip robots
    return f


@respx.mock
def test_gleif_lookup_maps_org_nr(tmp_path):
    payload = json.loads((FIX / "gleif_holmstrom.json").read_text())
    respx.get(url__startswith="https://api.gleif.org/api/v1/lei-records").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with fetcher(tmp_path) as f:
        df = gleif.lookup_leis(f, ["549300SHV46XGO189865"])
    row = df.row(0, named=True)
    assert row["org_nr"] == "559286-6809"
    assert row["country"] == "SE"
    assert row["legal_name"] == "Holmström Fastigheter Holding AB (publ)"


@respx.mock
def test_riksbank_rates_shape(tmp_path):
    respx.get(url__regex=r".*/swea/v1/Observations/.*").mock(
        return_value=httpx.Response(200, json=[{"date": "2026-10-07", "value": 1.75}])
    )
    respx.get(url__regex=r".*/swestr/v1/all/SWESTR.*").mock(
        return_value=httpx.Response(200, json=[{"date": "2026-10-07", "rate": 1.637}])
    )
    with fetcher(tmp_path) as f:
        df = riksbank.fetch_rates(f, date(2026, 10, 1), date(2026, 10, 8))
    assert set(df["series"]) == {"policy_rate", "tbill_3m", "swestr"}
    assert df.filter(df["series"] == "swestr")["value"][0] == 1.637


@respx.mock
def test_cache_avoids_second_request(tmp_path):
    route = respx.get("https://api.riksbank.se/x").mock(return_value=httpx.Response(200, json=[]))
    with fetcher(tmp_path) as f:
        f.get("https://api.riksbank.se/x")
        f.get("https://api.riksbank.se/x")
    assert route.call_count == 1


def test_classify_property_names():
    assert classify("Holmström Fastigheter Holding AB (publ)") == "name keyword"
    assert classify("AB Sagax") == "seed list"
    assert classify("Swedbank Hypotek AB") is None  # mortgage lender
    assert classify("Fastighetsräntefonden Tessin AB (publ)") is None  # property-debt fund
    assert classify("Volvo Car AB") is None
