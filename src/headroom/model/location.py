"""Locations: indicators, scores, peers, headlines and the place index for search.

Everything here is a pure function of the location snapshot (data/snapshots/locations)
and config/weights.yaml. Scores are rule-based: a linear ramp per component, weights that
sum to one, missing components dropped and the rest re-weighted. No model or LLM decides
a figure or a score.

Levels: riket (Sweden), lan (county), kommun (municipality), tatort (city), regso (area).
A city has no statistics of its own beyond its population; its page shows the
municipality's figures and the RegSO that make up the city.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import polars as pl
import yaml

from headroom.config import CONFIG_DIR
from headroom.model.scoring import ramp

GEO_DIR = CONFIG_DIR / "geo"

LEVEL_LABEL = {
    "riket": "Country",
    "lan": "County",
    "kommun": "Municipality",
    "tatort": "City",
    "regso": "Area",
}

# --------------------------------------------------------------------------- helpers


def series(ind: pl.DataFrame, name: str) -> pl.DataFrame:
    """code, period, value for one indicator (nulls dropped), sorted."""
    return (
        ind.filter((pl.col("indicator") == name) & pl.col("value").is_not_null())
        .select("code", "period", "value")
        .sort("code", "period")
    )


def as_map(df: pl.DataFrame) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for c, p, v in df.iter_rows():
        out.setdefault(c, {})[p] = v
    return out


def latest_of(m: dict[str, float]) -> tuple[str, float] | None:
    if not m:
        return None
    p = max(m)
    return p, m[p]


def annual(q: dict[str, float]) -> dict[str, float]:
    """Quarterly values ('2025K1') -> calendar-year sums, complete years only."""
    years: dict[str, list[float]] = {}
    for p, v in q.items():
        years.setdefault(p[:4], []).append(v)
    return {y: sum(v) for y, v in years.items() if len(v) == 4}


def cagr(a: float | None, b: float | None, years: float) -> float | None:
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return (b / a) ** (1 / years) - 1


def ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


@lru_cache
def load_utsatta() -> list[dict]:
    p = GEO_DIR / "utsatta_omraden.yaml"
    return yaml.safe_load(p.read_text("utf-8"))["areas"] if p.exists() else []


@lru_cache
def utsatta_meta() -> dict:
    p = GEO_DIR / "utsatta_omraden.yaml"
    d = yaml.safe_load(p.read_text("utf-8")) if p.exists() else {}
    return {k: d.get(k) for k in ("source", "source_url", "published")}


@lru_cache
def regso_changed() -> frozenset[str]:
    """RegSO codes whose borders changed from the 2020 to the 2025 version (SCB's change
    file); their series break in 2024. Name changes and extensions into territorial
    water keep the series."""
    p = GEO_DIR / "regso_changes.csv"
    if not p.exists():
        return frozenset()
    df = pl.read_csv(p, schema_overrides={"kommun": pl.Utf8})
    keep = {"Namnändrad (ej kodändrad)", "Utvidgas till territorialvattnet"}
    df = df.filter(~pl.col("change").is_in(list(keep)))
    return frozenset(df["old_code"].to_list() + df["new_code"].to_list())


@lru_cache
def logistics_ranking() -> list[dict]:
    p = GEO_DIR / "logistics_ranking.yaml"
    return yaml.safe_load(p.read_text("utf-8"))["editions"] if p.exists() else []


def ranking_for(level: str, code: str) -> list[dict]:
    """Ranking entries that cover a county or municipality (newest edition first)."""
    out = []
    lan = code[:2]
    for ed in logistics_ranking():
        for pl_ in ed["places"]:
            hit = (
                level in ("kommun", "tatort", "regso") and code[:4] in pl_.get("kommuner", [])
            ) or (lan in pl_.get("counties", []))
            if hit:
                out.append(
                    {**pl_, **{k: ed[k] for k in ("year", "title", "publisher", "source_url")}}
                )
    return sorted(out, key=lambda r: -r["year"])


# --------------------------------------------------------------------------- features

ADMIN = ("riket", "lan", "kommun")


def features(
    loc: pl.DataFrame, ind: pl.DataFrame, utsatta: list[dict] | None = None
) -> pl.DataFrame:
    """One row per place with the derived indicators used by scores, tables and maps.

    Each feature `x` comes with `x_period` (the period it describes) where useful.
    Values that cannot be computed are null, never zero.
    """
    S = {name: as_map(series(ind, name)) for name in ind["indicator"].unique().to_list()}
    notes = {
        (r["code"], r["indicator"]): r["note"]
        for r in ind.filter(pl.col("indicator").str.starts_with("dist_"))
        .select("code", "indicator", "note")
        .iter_rows(named=True)
    }

    def get(name: str, code: str) -> dict[str, float]:
        return S.get(name, {}).get(code, {})

    riket = "00"
    rows: list[dict[str, Any]] = []
    changed = regso_changed()
    ut_by_regso: dict[str, dict] = {}
    ut_by_kommun: dict[str, list[dict]] = {}
    for a in utsatta or []:
        ut_by_kommun.setdefault(a["kommun"], []).append(a)
        for r in a.get("regso") or []:
            prev = ut_by_regso.get(r)
            if prev is None or a["category"] == "sarskilt_utsatt":
                ut_by_regso[r] = a
    for place in loc.iter_rows(named=True):
        code, level = place["code"], place["level"]
        f: dict[str, Any] = {"code": code, "level": level}
        pop = get("pop", code)
        lp = latest_of(pop)
        if lp:
            y = int(lp[0])
            f["population"], f["population_year"] = lp[1], lp[0]
            prev = pop.get(str(y - 1))
            f["pop_change_1y"] = lp[1] - prev if prev is not None else None
            f["growth_1y"] = cagr(prev, lp[1], 1)
            f["growth_5y"] = cagr(pop.get(str(y - 5)), lp[1], 5)
            f["growth_10y"] = cagr(pop.get(str(y - 10)), lp[1], 10)
            young = get("pop_20_34", code).get(lp[0])
            f["share_20_34"] = ratio(young, lp[1])
        if level in ADMIN and lp:
            y = int(lp[0])
            comp_y = annual(get("completions_q", code))
            yrs = [str(y - i) for i in range(3)]
            if all(k in comp_y for k in yrs) and str(y - 3) in pop:
                built = sum(comp_y[k] for k in yrs)
                f["housing_pressure"] = ratio(lp[1] - pop[str(y - 3)], built)
                f["housing_pressure_period"] = f"{y - 2}–{y}"
                hh = get("households", code)
                if lp[0] in hh and str(y - 3) in hh:
                    f["household_pressure"] = ratio(hh[lp[0]] - hh[str(y - 3)], built)
            starts = get("starts_q", code)
            if len(starts) >= 4:
                last4 = sorted(starts)[-4:]
                f["pipeline"] = sum(starts[q] for q in last4) / lp[1] * 1000
                f["pipeline_period"] = f"{last4[0]}–{last4[-1]}"
                comps = get("completions_q", code)
                if all(q in comps for q in last4):
                    f["completions_per_1000"] = sum(comps[q] for q in last4) / lp[1] * 1000
            for key, ind_name in (
                ("young_inflow", "dom_net_20_34"),
                ("dom_net_per_1000", "dom_net"),
                ("int_net_per_1000", "int_net"),
            ):
                m = get(ind_name, code)
                if m:
                    last = sorted(m)[-3:]
                    vals = [m[p] / pop[p] * 1000 for p in last if pop.get(p)]
                    if vals:
                        f[key] = sum(vals) / len(vals)
                        f[f"{key}_period"] = f"{last[0]}–{last[-1]}"
                    f[f"{key}_last"] = m[sorted(m)[-1]]
            dw = latest_of(get("dwellings", code))
            rent = get("dwellings_rental", code)
            if dw:
                f["rental_share"] = ratio(rent.get(dw[0]), dw[1])
                f["rental_share_period"] = dw[0]
                f["dwellings"] = dw[1]
            c_all, c_rent = get("completions", code), get("completions_rental", code)
            if c_all:
                last5 = sorted(c_all)[-5:]
                f["rental_share_new"] = ratio(
                    sum(c_rent.get(p, 0) for p in last5), sum(c_all[p] for p in last5)
                )
            for k in (
                "bme",
                "bme_students",
                "bme_young",
                "unemployment",
                "tax_base_pct",
                "transit_500m",
                "income_median",
                "net_income_mean",
                "grp_per_capita",
                "low_econ_std",
            ):
                v = latest_of(get(k, code))
                if v:
                    f[k], f[f"{k}_period"] = v[1], v[0]
            bme = get("bme", code)
            if bme:
                p = max(bme)
                p3 = str(int(p) - 3)
                if p3 in bme:
                    f["bme_trend"] = bme[p] - bme[p3]
            r = latest_of(get("rent_sqm", code))
            if r:
                f["rent_sqm"], f["rent_sqm_period"] = r[1], r[0]
                f["rent_growth_5y"] = cagr(get("rent_sqm", code).get(str(int(r[0]) - 5)), r[1], 5)
            edu = latest_of(get("edu_post3", code))
            if edu:
                f["education"] = ratio(edu[1], get("pop_25_65", code).get(edu[0]))
                f["education_period"] = edu[0]
            # jobs at workplaces in the place (day population)
            tot = get("emp_work_total", code)
            h = get("emp_work_H", code)
            lt = latest_of(tot)
            if lt and lt[0] in h:
                f["jobs"] = lt[1]
                f["jobs_period"] = lt[0]
                f["logistics_jobs"] = h[lt[0]]
                f["logistics_share"] = ratio(h[lt[0]], lt[1])
                first = min(h)
                f["logistics_growth"] = cagr(h[first], h[lt[0]], int(lt[0]) - int(first))
                f["logistics_growth_period"] = f"{first}–{lt[0]}"
                bc, fb = get("emp_work_BC", code), get("emp_work_F", code)
                if lt[0] in bc and lt[0] in fb:
                    f["industrial_share"] = ratio(bc[lt[0]] + fb[lt[0]], lt[1])
                res = get("emp_res_total", code).get(lt[0])
                f["jobs_ratio"] = ratio(lt[1], res)
            cin, cout = latest_of(get("commuters_in", code)), latest_of(get("commuters_out", code))
            if cin and cout:
                f["job_hub"] = ratio(cin[1], cout[1])
                f["job_hub_period"] = cin[0]
                f["commuters_in"], f["commuters_out"] = cin[1], cout[1]
            grp = get("grp", code)
            g = latest_of(grp)
            if g:
                f["grp_growth_5y"] = cagr(grp.get(str(int(g[0]) - 5)), g[1], 5)
                f["grp_period"] = g[0]
            proj = get("pop_projection", code)
            if "2035" in proj and "2025" in proj:
                f["projected_growth_2035"] = cagr(proj["2025"], proj["2035"], 10)
            be = latest_of(get("business_area_employees", code))
            if be:
                f["industrial_cluster"] = be[1] / lp[1] * 1000
                f["business_area_employees"] = be[1]
            land = latest_of(get("industrial_land", code))
            if land:
                f["industrial_land"] = land[1]
                f["industrial_land_period"] = land[0]
            ia = latest_of(get("industrial_assessed_per_unit", code))
            if ia:
                f["industrial_assessed_per_unit"], f["industrial_assessed_period"] = ia[1], ia[0]
            lan = code[:2] if level == "kommun" else code
            for k in ("industrial_kt", "warehouse_kt", "rental_block_kt", "house_kt"):
                v = latest_of(get(k, lan if level != "riket" else riket))
                if v:
                    f[k], f[f"{k}_period"] = v[1], v[0]
            ut = (
                ut_by_kommun.get(code, [])
                if level == "kommun"
                else [
                    a
                    for k, v in ut_by_kommun.items()
                    if k[:2] == code or level == "riket"
                    for a in v
                ]
            )
            f["utsatta_n"] = len(ut)
            f["sarskilt_utsatta_n"] = sum(a["category"] == "sarskilt_utsatt" for a in ut)
        if level == "regso":
            pop25 = get("pop", code)
            p20 = pop25.get("2020")
            f["series_break"] = code in changed
            if lp and lp[0] >= "2024" and p20 is not None and code not in changed:
                f["growth"] = cagr(p20, lp[1], int(lp[0]) - 2020)
                f["growth_basis"] = f"2020–{lp[0]}"
            elif lp and f.get("growth_1y") is not None:
                f["growth"] = f["growth_1y"]
                f["growth_basis"] = f"{int(lp[0]) - 1}–{lp[0]}"
            if code in changed:
                f["growth_5y"] = None
                f["growth_10y"] = None
            dw = latest_of(get("dwellings", code))
            if dw:
                f["rental_share"] = ratio(get("dwellings_rental", code).get(dw[0]), dw[1])
                f["condo_share"] = ratio(get("dwellings_condo", code).get(dw[0]), dw[1])
                f["rental_share_period"] = dw[0]
                f["dwellings"] = dw[1]
            for k in ("net_income_mean", "low_econ_std", "emp_rate", "sei", "area_type"):
                v = latest_of(get(k, code))
                if v:
                    f[k], f[f"{k}_period"] = v[1], v[0]
            sei = get("sei", code)
            at = get("area_type", code)
            if sei and code not in changed:
                last = max(sei)
                base = str(int(last) - 10)
                if base in sei:
                    # SCB's index is the mean of three shares in percent: higher is worse.
                    # Improvement = fall in the index, in percentage points.
                    f["sei_improvement"] = sei[base] - sei[last]
                    f["sei_improvement_period"] = f"{base}–{last}"
                if base in at and last in at:
                    f["area_type_then"] = at[base]
            edu = latest_of(get("edu_post3", code))
            if edu:
                f["education"] = ratio(edu[1], get("pop_25_65", code).get(edu[0]))
            mig = get("dom_net_pct", code)
            if mig and code not in changed:
                last = sorted(mig)[-3:]
                f["dom_net_pct"] = sum(mig[p] for p in last) / len(last)
                f["dom_net_pct_period"] = f"{last[0]}–{last[-1]}"
            a = ut_by_regso.get(code)
            f["utsatt"] = a["category"] if a else None
            f["utsatt_name"] = a["name"] if a else None
            f["utsatt_trend"] = a["trend"] if a else None
        for k in ("catchment_50km", "catchment_100km", "catchment_200km"):
            v = latest_of(get(k, code))
            if v:
                f[k] = v[1]
        for kind in ("port", "terminal", "airport", "motorway"):
            v = latest_of(get(f"dist_{kind}", code))
            if v:
                f[f"dist_{kind}"] = v[1]
                f[f"dist_{kind}_name"] = notes.get((code, f"dist_{kind}"))
        d = [f.get("dist_port"), f.get("dist_terminal"), f.get("dist_airport")]
        d = [x for x in d if x is not None]
        f["node_distance"] = min(d) if d else None
        f["motorway_distance"] = f.get("dist_motorway")
        rows.append(f)
    # Kolada has no housing-market assessment for counties and Sweden: use the mean of
    # the municipalities' answers (0 shortage, 1 balance, 2 surplus), marked as derived.
    kb = [r for r in rows if r["level"] == "kommun" and r.get("bme") is not None]
    for r in rows:
        if r["level"] in ("lan", "riket") and r.get("bme") is None:
            vals = [x["bme"] for x in kb if r["level"] == "riket" or x["code"][:2] == r["code"]]
            if vals:
                r["bme"] = sum(vals) / len(vals)
                r["bme_period"] = max(x["bme_period"] for x in kb)
                r["bme_derived"] = True
    out = pl.DataFrame(rows, infer_schema_length=None)
    # relative measures need Sweden's value
    nat = out.filter(pl.col("code") == riket)
    if nat.height:
        n = nat.row(0, named=True)
        if n.get("income_median"):
            out = out.with_columns(
                (pl.col("income_median") / n["income_median"]).alias("income_rel")
            )
        if n.get("logistics_share"):
            out = out.with_columns(
                (pl.col("logistics_share") / n["logistics_share"]).alias("logistics_lq")
            )
    # RegSO income relative to its municipality
    if "net_income_mean" in out.columns:
        kin = out.filter(pl.col("level") == "kommun").select(
            pl.col("code").alias("parent"), pl.col("net_income_mean").alias("_k_inc")
        )
        out = (
            out.with_columns(
                pl.when(pl.col("level") == "regso")
                .then(pl.col("code").str.slice(0, 4))
                .otherwise(None)
                .alias("parent")
            )
            .join(kin, on="parent", how="left")
            .with_columns(
                pl.when(pl.col("level") == "regso")
                .then(pl.col("net_income_mean") / pl.col("_k_inc"))
                .otherwise(None)
                .alias("income_rel_kommun")
            )
            .drop("_k_inc", "parent")
        )
    for col in (
        "income_rel",
        "logistics_lq",
        "income_rel_kommun",
        "sei_improvement",
        "growth",
        "utsatt",
    ):
        if col not in out.columns:
            out = out.with_columns(pl.lit(None).alias(col))
    return out


# --------------------------------------------------------------------------- scores

SCORE_KINDS = {
    "residential": ("residential", ("lan", "kommun", "riket")),
    "residential_regso": ("residential", ("regso",)),
    "logistics": ("logistics", ("lan", "kommun", "riket")),
}

COMPONENT_LABEL = {
    "growth_5y": "Population growth, 5 years",
    "housing_pressure": "Residents per new home",
    "young_inflow": "Inflow of 20–34-year-olds",
    "bme": "Housing-market assessment",
    "pipeline": "Homes started (new supply)",
    "income_rel": "Median income vs Sweden",
    "education": "Higher education",
    "growth": "Population growth",
    "share_20_34": "Share aged 20–34",
    "rental_share": "Rental share",
    "sei_improvement": "Socio-economic improvement, 10 years",
    "income_rel_kommun": "Income vs municipality",
    "kommun_context": "Municipality score",
    "catchment_100km": "Population within 100 km",
    "logistics_lq": "Logistics jobs vs Sweden",
    "logistics_growth": "Logistics job growth",
    "node_distance": "Nearest port, terminal or cargo airport",
    "motorway_distance": "Nearest motorway junction",
    "job_hub": "In- vs out-commuters",
    "industrial_cluster": "Business-area jobs per 1,000",
}


@dataclass
class ScoreResult:
    score: float | None
    coverage: float
    components: list[dict]


def score_one(values: dict[str, Any], block: dict, min_coverage: float) -> ScoreResult:
    comps = []
    total_w = sum(block["weights"].values())
    have_w = 0.0
    acc = 0.0
    for key, w in block["weights"].items():
        spec = block.get(key, {})
        x = values.get(key)
        if x is None or (isinstance(x, float) and math.isnan(x)):
            comps.append(
                {
                    "key": key,
                    "label": COMPONENT_LABEL.get(key, key),
                    "weight": w,
                    "value": None,
                    "score": None,
                }
            )
            continue
        xv = float(x)
        z, full = spec["zero"], spec["full"]
        if spec.get("scale") == "log":
            if xv <= 0:
                comps.append(
                    {
                        "key": key,
                        "label": COMPONENT_LABEL.get(key, key),
                        "weight": w,
                        "value": xv,
                        "score": None,
                    }
                )
                continue
            xv, z, full = math.log10(xv), math.log10(z), math.log10(full)
        s = ramp(xv, z, full)
        have_w += w
        acc += w * s
        comps.append(
            {
                "key": key,
                "label": COMPONENT_LABEL.get(key, key),
                "weight": w,
                "value": float(x),
                "score": s,
            }
        )
    coverage = have_w / total_w if total_w else 0.0
    score = acc / have_w if have_w and coverage >= min_coverage else None
    return ScoreResult(score, coverage, comps)


def score_all(feats: pl.DataFrame, cfg: dict) -> pl.DataFrame:
    """Long table: code, level, score_kind, score, coverage, components_json."""
    lc = cfg["location"]
    min_cov = lc.get("min_coverage", 0.5)
    rows = []
    recs = {r["code"]: r for r in feats.iter_rows(named=True)}
    res_k: dict[str, float | None] = {}
    for kind, (label, levels) in SCORE_KINDS.items():
        if kind == "residential_regso":
            continue
        block = lc[kind]
        for code, r in recs.items():
            if r["level"] not in levels:
                continue
            sr = score_one(r, block, min_cov)
            if label == "residential":
                res_k[code] = sr.score
            rows.append(
                {
                    "code": code,
                    "level": r["level"],
                    "score_kind": label,
                    "score": sr.score,
                    "coverage": sr.coverage,
                    "components_json": json.dumps(sr.components),
                }
            )
    block = lc["residential_regso"]
    for code, r in recs.items():
        if r["level"] != "regso":
            continue
        vals = {**r, "kommun_context": res_k.get(code[:4])}
        sr = score_one(vals, block, min_cov)
        rows.append(
            {
                "code": code,
                "level": "regso",
                "score_kind": "residential",
                "score": sr.score,
                "coverage": sr.coverage,
                "components_json": json.dumps(sr.components),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "code": pl.Utf8,
            "level": pl.Utf8,
            "score_kind": pl.Utf8,
            "score": pl.Float64,
            "coverage": pl.Float64,
            "components_json": pl.Utf8,
        },
    )


def weights_ok(cfg: dict) -> dict[str, float]:
    """Sum of weights per location score block (each should be 1.0)."""
    lc = cfg["location"]
    return {
        k: round(sum(lc[k]["weights"].values()), 6)
        for k in ("residential", "residential_regso", "logistics")
    }


# --------------------------------------------------------------------------- comparisons


def percentile(values: list[float | None], x: float | None, min_n: int = 20) -> float | None:
    """Share of comparable values below x (ties count half), 0-100; None when fewer than
    `min_n` values exist."""
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if x is None or len(vals) < min_n:
        return None
    below = sum(v < x for v in vals)
    equal = sum(v == x for v in vals)
    return 100.0 * (below + 0.5 * equal) / len(vals)


TWIN_FEATURES = ["log_population", "growth_5y", "income_median", "rental_share", "logistics_share"]


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def nearest(loc: pl.DataFrame, code: str, level: str, n: int = 5) -> list[tuple[str, float, bool]]:
    """n nearest places of the same level by centroid; places in the same local labour
    market (LA 2018) first. Returns (code, km, same_la)."""
    pts = loc.filter(pl.col("level") == level)
    me = pts.filter(pl.col("code") == code)
    if me.is_empty():
        return []
    m = me.row(0, named=True)
    out = []
    for r in pts.iter_rows(named=True):
        if r["code"] == code or r["lat"] is None:
            continue
        same = bool(m.get("la_region")) and r.get("la_region") == m.get("la_region")
        out.append((r["code"], _km((m["lat"], m["lon"]), (r["lat"], r["lon"])), same))
    out.sort(key=lambda t: (not t[2], t[1]))
    return out[:n]


def twins(feats: pl.DataFrame, code: str, level: str, k: int = 5) -> list[tuple[str, float]]:
    """k nearest neighbours on standardised indicators (log population, 5-year growth,
    median income, rental share, logistics share). Places missing any of them are left out."""
    f = feats.filter(pl.col("level") == level)
    if "population" not in f.columns:
        return []
    f = f.with_columns(pl.col("population").log10().alias("log_population"))
    cols = [c for c in TWIN_FEATURES if c in f.columns]
    f = f.select("code", *cols).drop_nulls()
    if f.height < k + 1 or code not in f["code"].to_list():
        return []
    x = f.select(cols).to_numpy().astype(float)
    sd = x.std(axis=0)
    sd[sd == 0] = 1.0
    z = (x - x.mean(axis=0)) / sd
    codes = f["code"].to_list()
    i = codes.index(code)
    d = np.sqrt(((z - z[i]) ** 2).sum(axis=1))
    order = [j for j in np.argsort(d) if j != i][:k]
    return [(codes[j], float(d[j])) for j in order]


def peer_table(loc: pl.DataFrame, feats: pl.DataFrame, kolada_groups: pl.DataFrame) -> pl.DataFrame:
    """location_peer: code, level, peer_set, peer_code, rank, distance, reason."""
    rows = []
    names = dict(zip(loc["code"], loc["name"], strict=True))
    for level in ("kommun", "lan"):
        codes = loc.filter(pl.col("level") == level)["code"].to_list()
        for code in codes:
            for i, (pc, km, same) in enumerate(
                nearest(loc, code, level, 5 if level == "kommun" else 3), start=1
            ):
                la = (
                    loc.filter(pl.col("code") == code)["la_name"].to_list()[0]
                    if "la_name" in loc.columns
                    else None
                )
                reason = f"{km:.0f} km away" + (
                    f", same local labour market ({la})" if same and la else ""
                )
                rows.append(
                    {
                        "code": code,
                        "level": level,
                        "peer_set": "nearest",
                        "peer_code": pc,
                        "rank": i,
                        "distance": km,
                        "reason": reason,
                    }
                )
            for i, (pc, d) in enumerate(
                twins(feats, code, level, 5 if level == "kommun" else 3), start=1
            ):
                rows.append(
                    {
                        "code": code,
                        "level": level,
                        "peer_set": "twin",
                        "peer_code": pc,
                        "rank": i,
                        "distance": d,
                        "reason": "Similar size, growth, income, rental share and logistics jobs",
                    }
                )
    if kolada_groups.height:
        for code, grp in kolada_groups.group_by("code"):
            c = code[0] if isinstance(code, tuple) else code
            for i, r in enumerate(grp.iter_rows(named=True), start=1):
                rows.append(
                    {
                        "code": c,
                        "level": "kommun",
                        "peer_set": "kolada",
                        "peer_code": r["peer_code"],
                        "rank": i,
                        "distance": None,
                        "reason": r["title"],
                    }
                )
    del names
    return pl.DataFrame(
        rows,
        schema={
            "code": pl.Utf8,
            "level": pl.Utf8,
            "peer_set": pl.Utf8,
            "peer_code": pl.Utf8,
            "rank": pl.Int64,
            "distance": pl.Float64,
            "reason": pl.Utf8,
        },
    )


# --------------------------------------------------------------------------- statements


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%".replace("-", "−")


def _num(x: float) -> str:
    return f"{abs(x):,.0f}"


def headline(name: str, f: dict, nat: dict) -> str:
    """A rule-based one-line statement: the indicator where the place differs most from
    Sweden, in plain words. Candidates are compared on how far they are from Sweden in
    units of the national spread (passed as `nat['_sd_<key>']`)."""
    cands: list[tuple[float, str]] = []

    def dev(key: str) -> float | None:
        x, n, sd = f.get(key), nat.get(key), nat.get(f"_sd_{key}")
        if x is None or n is None or not sd:
            return None
        return (x - n) / sd

    d = dev("growth_1y")
    if d is not None and f.get("pop_change_1y") is not None and nat.get("growth_1y"):
        g, n = f["growth_1y"], nat["growth_1y"]
        verb = "added" if f["pop_change_1y"] >= 0 else "lost"
        if n > 0 and g > 0:
            k = g / n
            pace = (
                "twice the national pace"
                if 1.8 <= k < 2.3
                else f"{k:.1f} times the national pace"
                if k >= 1.3
                else "about the national pace"
                if k >= 0.8
                else "below the national pace"
            )
        else:
            pace = f"against {_pct(n)} for Sweden"
        cands.append(
            (abs(d), f"{name} {verb} {_num(f['pop_change_1y'])} residents last year, {pace}.")
        )
    d = dev("housing_pressure")
    if d is not None:
        cands.append(
            (
                abs(d),
                f"{name} added {f['housing_pressure']:.1f} residents for every new home over "
                f"three years; Sweden added {nat['housing_pressure']:.1f}.",
            )
        )
    d = dev("young_inflow")
    if d is not None:
        word = "gained" if f["young_inflow"] >= 0 else "lost"
        cands.append(
            (
                abs(d),
                f"{name} {word} {abs(f['young_inflow']):.1f} people aged 20–34 per 1,000 "
                "residents a year from the rest of Sweden.",
            )
        )
    d = dev("logistics_lq")
    if d is not None and f.get("logistics_share") is not None:
        cands.append(
            (
                abs(d),
                f"Transport and warehousing hold {_pct(f['logistics_share'])} of jobs in "
                f"{name}, {f['logistics_lq']:.1f} times Sweden's share.",
            )
        )
    d = dev("growth_5y")
    if d is not None:
        word = "grown" if f["growth_5y"] >= 0 else "shrunk"
        cands.append(
            (
                abs(d) * 0.9,
                f"{name} has {word} {_pct(abs(f['growth_5y']))} a year for five years; "
                f"Sweden {_pct(nat['growth_5y'])}.",
            )
        )
    if not cands:
        pop = f.get("population")
        return f"{name} has {_num(pop)} residents." if pop else name
    return max(cands)[1]


# --------------------------------------------------------------------------- search index


def place_index(loc: pl.DataFrame) -> list[dict]:
    """Places for search, best tier first: counties and municipalities (tier 1), cities
    (tier 2), areas (tier 3). Each item: label, sub, value, tier, keys (strings to match)."""
    kname = dict(
        zip(
            loc.filter(pl.col("level") == "kommun")["code"],
            loc.filter(pl.col("level") == "kommun")["name"],
            strict=True,
        )
    )
    lname = dict(
        zip(
            loc.filter(pl.col("level") == "lan")["code"],
            loc.filter(pl.col("level") == "lan")["name"],
            strict=True,
        )
    )
    out = []
    for r in loc.iter_rows(named=True):
        lvl, code, name = r["level"], r["code"], r["name"]
        if lvl == "riket":
            continue
        if lvl == "lan":
            out.append(
                {
                    "label": name,
                    "sub": "County",
                    "value": f"lan:{code}",
                    "tier": 1,
                    "keys": [name, name.removesuffix(" län")],
                }
            )
        elif lvl == "kommun":
            out.append(
                {
                    "label": name,
                    "sub": f"Municipality · {lname.get(code[:2], '')}",
                    "value": f"kommun:{code}",
                    "tier": 1,
                    "keys": [name],
                }
            )
        elif lvl == "tatort":
            k = kname.get(r["parent_kommun"] or "", "")
            if name == k:  # the city that shares its municipality's name: one entry is enough
                continue
            out.append(
                {
                    "label": name,
                    "sub": f"City · {k} municipality",
                    "value": f"tatort:{code}",
                    "tier": 2,
                    "keys": [name],
                }
            )
        elif lvl == "regso":
            k = kname.get(r["parent_kommun"] or "", "")
            parts = [p.strip() for p in name.replace("/", "-").split("-") if p.strip()]
            out.append(
                {
                    "label": name,
                    "sub": f"Area · {k} municipality",
                    "value": f"regso:{code}",
                    "tier": 3,
                    "keys": [name, *parts],
                }
            )
    return out
