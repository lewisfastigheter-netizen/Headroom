"""Locations: SCB, Kolada and WFS clients against recorded-style responses (no network),
indicators, scores, peers, RegSO versions and place search."""

from __future__ import annotations

import json
import math

import httpx
import polars as pl
import pytest
import respx

from headroom.config import weights
from headroom.http import Fetcher
from headroom.model import location as L
from headroom.model.search import prepare, search_tiered
from headroom.sources import kolada, scb_geo
from headroom.sources.scb import BASE, SCB, parse_jsonstat, split_version, version_valid

# --------------------------------------------------------------------------- fixtures


def jsonstat(regions, periods, values, status=None):
    """A json-stat2 dataset with dimensions Region x ContentsCode x Tid."""
    return {
        "version": "2.0",
        "class": "dataset",
        "id": ["Region", "ContentsCode", "Tid"],
        "size": [len(regions), 1, len(periods)],
        "dimension": {
            "Region": {
                "category": {
                    "index": {r: i for i, r in enumerate(regions)},
                    "label": {r: r for r in regions},
                }
            },
            "ContentsCode": {"category": {"index": {"X1": 0}, "label": {"X1": "Antal"}}},
            "Tid": {
                "category": {
                    "index": {p: i for i, p in enumerate(periods)},
                    "label": {p: p for p in periods},
                }
            },
        },
        "value": values,
        "status": status or {},
    }


METADATA = {
    "label": "Test table",
    "updated": "2026-03-24T07:00:00Z",
    "id": ["Region", "ContentsCode", "Tid"],
    "role": {"time": ["Tid"]},
    "dimension": {
        "Region": {
            "category": {"index": {"0114": 0, "0114R001": 1, "0114R001_RegSO2025": 2}, "label": {}},
            "extension": {"codelists": [{"id": "vs_RegSO2025"}]},
        },
        "ContentsCode": {"category": {"index": {"X1": 0}, "label": {}}},
        "Tid": {"category": {"index": {"2022": 0, "2023": 1, "2024": 2, "2025": 3}, "label": {}}},
    },
}


def fetcher(tmp_path) -> Fetcher:
    f = Fetcher(cache_dir=tmp_path)
    f._last = {}
    return f


# --------------------------------------------------------------------------- SCB


def test_jsonstat_dots_are_null_not_zero():
    js = jsonstat(
        ["0114", "0115"], ["2024", "2025"], [10, None, 0, 5], status={"1": "..", "3": "."}
    )
    df, labels = parse_jsonstat(js)
    vals = dict(zip(zip(df["Region"], df["Tid"], strict=True), df["value"], strict=True))
    assert vals[("0114", "2024")] == 10
    assert vals[("0114", "2025")] is None  # '..' suppressed
    assert vals[("0115", "2024")] == 0  # a real zero stays zero
    assert vals[("0115", "2025")] is None  # '.' not applicable
    assert labels["ContentsCode"]["X1"] == "Antal"


def test_regso_versions():
    assert split_version("0114R001_RegSO2025") == ("0114R001", "2025")
    assert split_version("0114R001") == ("0114R001", "2020")
    assert split_version("0114A0010_DeSO2025") == ("0114A0010", "2025")
    assert split_version("0114") == ("0114", None)
    assert version_valid("2020", 2023) and not version_valid("2020", 2024)
    assert version_valid("2025", 2024) and not version_valid("2025", 2023)
    assert version_valid(None, 1999)


@respx.mock
def test_scb_chunks_masks_versions_and_caches(tmp_path):
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"maxDataCells": 6}))
    respx.get(f"{BASE}/tables/TABX/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    regions = ["0114", "0114R001", "0114R001_RegSO2025"]

    def answer(request):
        body = json.loads(request.content)
        tid = next(s for s in body["selection"] if s["variableCode"] == "Tid")["valueCodes"]
        # SCB returns 0 (not '..') for years outside a RegSO version's range
        vals = []
        for r in regions:
            for p in tid:
                vals.append(100 + int(p) - 2022 if r == "0114" else 7)
        return httpx.Response(200, json=jsonstat(regions, tid, vals))

    route = respx.post(f"{BASE}/tables/TABX/data").mock(side_effect=answer)
    with fetcher(tmp_path) as f:
        s = SCB(f, cache_dir=tmp_path)
        df = s.data("TABX", {"Region": regions, "ContentsCode": ["X1"], "Tid": "*"})
        assert route.call_count == 2  # 3 regions x 4 years = 12 cells > 6: split by period
        # the 2020 code keeps 2022-2023, the 2025 code keeps 2024-2025, under one code
        r = df.filter(pl.col("region") == "0114R001").sort("period")
        assert r["period"].to_list() == ["2022", "2023", "2024", "2025"]
        assert r["regso_version"].to_list() == ["2020", "2020", "2025", "2025"]
        assert df.filter(pl.col("region") == "0114").height == 4
        # unchanged `updated`: the second call is served from the Parquet cache
        s2 = SCB(f, cache_dir=tmp_path)
        s2.data("TABX", {"Region": regions, "ContentsCode": ["X1"], "Tid": "*"})
        assert route.call_count == 2


@respx.mock
def test_scb_backs_off_on_429(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"maxDataCells": 1000}))
    respx.get(f"{BASE}/tables/TABX/metadata").mock(return_value=httpx.Response(200, json=METADATA))
    route = respx.post(f"{BASE}/tables/TABX/data").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=jsonstat(["0114"], ["2025"], [1])),
        ]
    )
    with fetcher(tmp_path) as f:
        df = SCB(f, cache_dir=tmp_path).data(
            "TABX", {"Region": ["0114"], "ContentsCode": ["X1"], "Tid": ["2025"]}
        )
    assert route.call_count == 2
    assert df["value"].to_list() == [1.0]


# --------------------------------------------------------------------------- Kolada


@respx.mock
def test_kolada_pages_and_missing(tmp_path):
    page2 = "https://api.kolada.se/v3/data/kpi/U30446/year/2026?page=2"
    respx.get("https://api.kolada.se/v3/data/kpi/U30446/year/2025,2026").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "kpi": "U30446",
                        "period": 2026,
                        "municipality": "0380",
                        "values": [{"gender": "T", "value": 0.0, "status": ""}],
                    },
                    {
                        "kpi": "U30446",
                        "period": 2026,
                        "municipality": "0000",
                        "values": [{"gender": "T", "value": None, "status": "Missing"}],
                    },
                ],
                "next_url": page2,
            },
        )
    )
    respx.get(page2).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "kpi": "U30446",
                        "period": 2026,
                        "municipality": "0003",
                        "values": [{"gender": "T", "value": 1.0}],
                    }
                ],
                "next_url": None,
            },
        )
    )
    with fetcher(tmp_path) as f:
        df = kolada.kpi_values(f, "U30446", [2025, 2026])
    rows = {(r["level"], r["code"]): r["value"] for r in df.to_dicts()}
    assert rows[("kommun", "0380")] == 0.0  # shortage is coded 0: a value, not missing
    assert rows[("riket", "00")] is None
    assert rows[("lan", "03")] == 1.0


@respx.mock
def test_kolada_peer_group_latest_year(tmp_path):
    respx.get(url__startswith="https://api.kolada.se/v3/municipality_groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "id": "G1",
                        "title": "Liknande kommuner socioekonomi, Uppsala, 2022",
                        "members": [{"member_id": "0580", "member_title": "Linköping"}],
                    },
                    {
                        "id": "G2",
                        "title": "Liknande kommuner socioekonomi, Uppsala, 2024",
                        "members": [{"member_id": "0680", "member_title": "Jönköping"}],
                    },
                    {"id": "G3", "title": "Något annat", "members": []},
                ],
                "next_url": None,
            },
        )
    )
    with fetcher(tmp_path) as f:
        df = kolada.peer_groups(f)
    assert df["peer_code"].to_list() == ["0680"]
    assert df["year"].to_list() == ["2024"]


# --------------------------------------------------------------------------- WFS and geometry


@respx.mock
def test_wfs_pages(tmp_path):
    def feats(ids):
        return {
            "type": "FeatureCollection",
            "numberMatched": 3,
            "features": [
                {"type": "Feature", "properties": {"id": i}, "geometry": None} for i in ids
            ],
        }

    route = respx.get(url__startswith=scb_geo.WFS).mock(
        side_effect=[httpx.Response(200, json=feats([1, 2])), httpx.Response(200, json=feats([3]))]
    )
    with fetcher(tmp_path) as f:
        out = scb_geo.features(f, "Tatorter_2023", page=2, ttl=None)
    assert [x["properties"]["id"] for x in out] == [1, 2, 3]
    assert route.call_count == 2
    assert "startIndex=2" in str(route.calls[1].request.url)


def test_sweref_roundtrip():
    e, n = scb_geo.wgs84_to_grid(59.3293, 18.0686)  # Stockholm
    assert abs(float(e) - 674572) < 5 and abs(float(n) - 6580743) < 5
    lat, lon = scb_geo.grid_to_wgs84(e, n)
    assert abs(float(lat) - 59.3293) < 1e-7 and abs(float(lon) - 18.0686) < 1e-7


def test_population_within_radius():
    import numpy as np

    grid = (
        np.array([0.0, 30_000.0, 120_000.0]),
        np.array([0.0, 0.0, 0.0]),
        np.array([10.0, 20.0, 40.0]),
    )
    out = scb_geo.within(np.array([0.0]), np.array([0.0]), grid, 50_000)
    assert out.tolist() == [30.0]


# --------------------------------------------------------------------------- indicators


def ind(rows):
    return pl.DataFrame(
        [
            {
                "code": c,
                "indicator": i,
                "period": p,
                "value": v,
                "level": None,
                "unit": "",
                "source": "SCB",
                "source_table": "T",
                "source_url": "",
                "as_of": "",
                "note": None,
            }
            for c, i, p, v in rows
        ],
        schema_overrides={"value": pl.Float64},
    )


def loc(rows):
    return pl.DataFrame(
        [
            {
                "level": lv,
                "code": c,
                "name": n,
                "display_name": n,
                "parent_kommun": pk,
                "parent_lan": c[:2],
                "regso_version": None,
                "tatort_codes": None,
                "lat": la,
                "lon": lo,
                "la_region": la_r,
            }
            for lv, c, n, pk, la, lo, la_r in rows
        ]
    )


def test_features_pressure_growth_and_regso_break():
    places = loc(
        [
            ("riket", "00", "Sweden", None, 62.0, 15.0, None),
            ("kommun", "0380", "Uppsala", None, 59.86, 17.64, "LA1801"),
            ("regso", "0380R001", "A", "0380", 59.8, 17.6, None),
            ("regso", "0380R002", "B", "0380", 59.9, 17.7, None),
        ]
    )
    rows = [
        ("0380", "pop", "2022", 1000.0),
        ("0380", "pop", "2025", 1300.0),
        ("0380", "pop", "2020", 900.0),
        *[
            ("0380", "completions_q", f"{y}K{q}", 25.0)
            for y in (2023, 2024, 2025)
            for q in (1, 2, 3, 4)
        ],
        ("0380R001", "pop", "2020", 100.0),
        ("0380R001", "pop", "2024", 110.0),
        ("0380R001", "pop", "2025", 121.0),
        ("0380R002", "pop", "2020", 100.0),
        ("0380R002", "pop", "2024", 200.0),
        ("0380R002", "pop", "2025", 210.0),
        ("0380R001", "sei", "2014", 10.0),
        ("0380R001", "sei", "2024", 7.0),
    ]
    f = L.features(places, ind(rows), [])
    k = f.filter(pl.col("code") == "0380").row(0, named=True)
    assert k["housing_pressure"] == pytest.approx(
        300 / 300
    )  # 300 more residents, 300 homes in 2023-2025
    assert k["growth_5y"] == pytest.approx((1300 / 900) ** 0.2 - 1)
    a = f.filter(pl.col("code") == "0380R001").row(0, named=True)
    assert a["growth"] == pytest.approx((121 / 100) ** 0.2 - 1)
    assert a["sei_improvement"] == pytest.approx(3.0)  # index fell 3 points: better
    # a RegSO whose borders changed in 2025 gets no growth across the break
    changed = L.regso_changed()
    assert changed  # config/geo/regso_changes.csv is committed
    if changed:
        code = sorted(changed)[0]
        places2 = loc(
            [
                ("riket", "00", "Sweden", None, 62.0, 15.0, None),
                ("regso", code, "X", code[:4], 59.0, 17.0, None),
            ]
        )
        rows2 = [
            (code, "pop", "2020", 100.0),
            (code, "pop", "2024", 150.0),
            (code, "pop", "2025", 160.0),
        ]
        b = L.features(places2, ind(rows2), []).filter(pl.col("code") == code).row(0, named=True)
        assert b["series_break"] is True
        assert b["growth_basis"] == "2024–2025"  # no 2020 base across the break


def test_annual_needs_four_quarters():
    q = {"2024K1": 1.0, "2024K2": 1.0, "2024K3": 1.0, "2024K4": 1.0, "2025K1": 5.0}
    assert L.annual(q) == {"2024": 4.0}


# --------------------------------------------------------------------------- scores


def test_location_weights_sum_to_one():
    for k, total in L.weights_ok(weights()).items():
        assert total == pytest.approx(1.0), k


def test_score_reweights_missing_and_respects_coverage():
    block = {
        "weights": {"a": 0.5, "b": 0.3, "c": 0.2},
        "a": {"zero": 0, "full": 10},
        "b": {"zero": 0, "full": 10},
        "c": {"zero": 10, "full": 0},
    }
    r = L.score_one({"a": 10, "b": 5, "c": None}, block, 0.5)
    assert r.coverage == pytest.approx(0.8)
    assert r.score == pytest.approx((0.5 * 100 + 0.3 * 50) / 0.8)
    assert L.score_one({"a": None, "b": None, "c": 0}, block, 0.5).score is None  # 20% < 50%
    log = {"weights": {"x": 1.0}, "x": {"zero": 1000, "full": 100000, "scale": "log"}}
    assert L.score_one({"x": 10000}, log, 0.5).score == pytest.approx(50.0)


def test_bme_shortage_scores_full():
    lc = weights()["location"]["residential"]
    assert L.score_one({"bme": 0.0}, {"weights": {"bme": 1.0}, "bme": lc["bme"]}, 0).score == 100
    assert L.score_one({"bme": 2.0}, {"weights": {"bme": 1.0}, "bme": lc["bme"]}, 0).score == 0


def test_percentile_needs_enough_values():
    assert L.percentile(list(range(10)), 5, min_n=20) is None
    assert L.percentile(list(range(100)), 50, min_n=20) == pytest.approx(50.5)
    assert L.percentile([None, math.nan, *range(30)], 0, min_n=20) == pytest.approx(100 * 0.5 / 30)


# --------------------------------------------------------------------------- peers


def test_nearest_prefers_same_labour_market():
    places = loc(
        [
            ("kommun", "0001", "A", None, 59.0, 18.0, "LA1"),
            ("kommun", "0002", "B", None, 59.05, 18.0, "LA2"),  # closest, other labour market
            ("kommun", "0003", "C", None, 59.3, 18.0, "LA1"),
        ]
    )
    out = L.nearest(places, "0001", "kommun", 2)
    assert [c for c, _, _ in out] == ["0003", "0002"]
    assert out[0][2] is True


def test_twins_on_standardised_features():
    rows = []
    for i, (pop, g) in enumerate(
        [
            (10_000, 0.0),
            (11_000, 0.001),
            (500_000, 0.02),
            (520_000, 0.019),
            (50_000, 0.005),
            (60_000, 0.006),
        ]
    ):
        rows.append(
            {
                "code": f"{i:04d}",
                "level": "kommun",
                "population": float(pop),
                "growth_5y": g,
                "income_median": 300.0 + i,
                "rental_share": 0.3,
                "logistics_share": 0.04,
            }
        )
    f = pl.DataFrame(rows)
    assert L.twins(f, "0002", "kommun", 1)[0][0] == "0003"


# --------------------------------------------------------------------------- search


def test_place_search_finds_regso_by_inner_name_and_ranks_tiers():
    places = loc(
        [
            ("lan", "03", "Uppsala län", None, 60.0, 17.6, None),
            ("kommun", "0380", "Uppsala", None, 59.86, 17.64, None),
            ("kommun", "0114", "Upplands Väsby", None, 59.5, 17.9, None),
            ("regso", "0114R001", "Bollstanäs", "0114", 59.5, 17.9, None),
            ("regso", "0380R030", "Luthagen", "0380", 59.86, 17.62, None),
            ("regso", "0380R031", "Uppsala centrum-Luthagen", "0380", 59.86, 17.63, None),
        ]
    )
    idx = L.place_index(places)
    entries = prepare([(i["label"], i["value"], i["tier"], i["keys"]) for i in idx])
    assert search_tiered("Bollstanäs", entries)[0][1] == "regso:0114R001"
    assert search_tiered("Luthagen", entries)[0][1] == "regso:0380R030"
    vals = [v for _, v in search_tiered("Uppsala", entries)]
    assert vals[0] in ("kommun:0380", "lan:03") and vals.index("kommun:0380") < vals.index(
        "regso:0380R031"
    )
    assert search_tiered("Bolstanäs", entries)[0][1] == "regso:0114R001"  # one typo
    assert search_tiered("xyzq", entries) == []
