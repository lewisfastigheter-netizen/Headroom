"""Locations: how counties (län), municipalities (kommuner) and areas (RegSO) develop, read
for a value-add investor in rental housing and logistics / light industrial property.

No place selected: the place search and the filters on the left (level, the places of
that level, county, municipality, group, population), a map on the right that lights up
the chosen places, and a table of them below.
A place selected (?level=kommun&code=0380): what is happening there, how it compares with
its neighbours, similar municipalities and Sweden, what that means for housing and for
logistics, the areas inside it, and the property companies that hold most of their
portfolio there.

All figures come from data/snapshots/locations (public statistics, the same in live and
demo mode). Missing values show as –; nothing is interpolated.
"""

from __future__ import annotations

import functools
import io
import json
import math
from urllib.parse import quote

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from headroom.model import geo as geo_ref
from headroom.model import location as L
from ui import components as ui
from ui.data import get_data
from ui.locdata import geojson, get_locations, index_for_session
from ui.place_search import place_search
from ui.plotly_template import BLUE, CONFIG, GRAPHITE, HIGH, INK, LIGHT_BLUE, MUTED, RULE
from ui.source_tips import source_tips

D = get_locations()
BLUE_RAMP = [
    [0.0, "#EEF6F8"],
    [0.25, "#C2E3E9"],
    [0.5, "#80C4D1"],
    [0.75, "#349DB1"],
    [1.0, "#006A7D"],
]
BME = {0: "Shortage", 1: "Balance", 2: "Surplus"}
NO_PERCENTILE = {"bme"}  # categorical
LEVEL_NOUN = {"lan": "county", "kommun": "municipality", "regso": "area"}


def nav(level: str, code: str) -> str:
    return f"locations?level={level}&code={code}"


def go_to(value: str | None) -> None:
    """'kommun:0380' -> reload the page on that place."""
    if not value or ":" not in value:
        return
    level, code = value.split(":", 1)
    st.query_params.clear()
    st.query_params["level"] = level
    st.query_params["code"] = code
    st.rerun()


def link(level: str, code: str, text: str) -> str:
    return f'<a href="{nav(level, code)}" target="_self">{ui.esc(text)}</a>'


def bme_text(v: float | None, exact: bool = True) -> str:
    if v is None:
        return "–"
    if exact and float(v).is_integer():
        return BME.get(int(v), "–")
    return "Mostly shortage" if v < 0.5 else "Mostly balance" if v <= 1.5 else "Mostly surplus"


def num(v, digits=1):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def period_txt(p: str | None) -> str:
    return f'<br><span style="color:var(--muted)">{ui.esc(str(p))}</span>' if p else ""


def src_line(indicators: list[str], extra: str = "", level: str | None = None) -> None:
    """'Source: SCB TAB6574 (2025), Kolada U30446 (2026)…' from the indicators used."""
    seen, parts = set(), []
    for name in indicators:
        s = D.source(name, level)
        if not s or s["source_table"] in seen:
            continue
        seen.add(s["source_table"])
        tbl = s["source_table"]
        label = f"{tbl} ({s['period']})"
        parts.append(f"{ui.esc(s['source'])} {ui.source_link(s['source_url'], label)}")
    text = "Source: " + "; ".join(parts) if parts else ""
    if extra:
        text = f"{text}. {extra}" if text else extra
    ui.source_line(text + ("" if text.endswith(".") else "."))


def level_feats(level: str) -> pl.DataFrame:
    return D.feats.filter(pl.col("level") == level)


def pct_rank(level: str, key: str, x) -> float | None:
    if key not in D.feats.columns:
        return None
    vals = level_feats(level)[key].to_list()
    return L.percentile(vals, x, D.cfg["location"].get("min_percentile_n", 20))


def score_txt(v: float | None) -> str:
    return "–" if v is None else f"{v:.0f}"


def score_state(v: float | None) -> str | None:
    if v is None:
        return None
    b = D.cfg["location"]["bands"]
    return "ok" if v >= b["high"] else None


# ---------------------------------------------------------------------------- source on hover

# feature -> (indicators it is computed from, how; "" when the figure is the indicator)
FEAT_SRC: dict[str, tuple[tuple[str, ...], str]] = {
    "population": (("pop",), ""),
    "growth": (("pop",), "Change in population per year"),
    "growth_1y": (("pop",), "Change in population over the last year"),
    "growth_5y": (("pop",), "Change in population per year over five years"),
    "growth_10y": (("pop",), "Change in population per year over ten years"),
    "housing_pressure": (
        ("pop", "completions_q"),
        "Population change over three years ÷ homes completed in the same years",
    ),
    "household_pressure": (
        ("households", "completions_q"),
        "Change in households over three years ÷ homes completed in the same years",
    ),
    "pipeline": (("starts_q", "pop"), "Homes started over four quarters per 1,000 residents"),
    "young_inflow": (
        ("dom_net_20_34", "pop"),
        "Net domestic migration of 20–34-year-olds per 1,000 residents, mean of three years",
    ),
    "int_net_per_1000": (("int_net", "pop"), "Net immigration per 1,000 residents"),
    "share_20_34": (("pop_20_34", "pop"), "Residents aged 20–34 ÷ all residents"),
    "bme": (("bme",), "The municipality's own assessment of its housing market"),
    "rental_share": (("dwellings_rental", "dwellings"), "Rental dwellings ÷ all dwellings"),
    "condo_share": (("dwellings_condo", "dwellings"), "Tenant-owned dwellings ÷ all dwellings"),
    "rental_share_new": (
        ("completions_rental", "completions"),
        "Rental flats ÷ all homes completed over five years",
    ),
    "rent_sqm": (("rent_sqm",), ""),
    "transit_500m": (("transit_500m",), ""),
    "income_median": (("income_median",), ""),
    "education": (("edu_post3", "pop_25_65"), "Residents 25–65 with 3+ years' higher education"),
    "unemployment": (("unemployment",), ""),
    "tax_base_pct": (("tax_base_pct",), ""),
    "catchment_50km": (("catchment_50km",), "Residents of 1 km squares within 50 km"),
    "catchment_100km": (("catchment_100km",), "Residents of 1 km squares within 100 km"),
    "catchment_200km": (("catchment_200km",), "Residents of 1 km squares within 200 km"),
    "dist_port": (("dist_port",), "Straight line from the population-weighted centre"),
    "dist_terminal": (("dist_terminal",), "Straight line from the population-weighted centre"),
    "dist_airport": (("dist_airport",), "Straight line from the population-weighted centre"),
    "logistics_jobs": (("emp_work_H",), "Jobs at workplaces here in transport and storage"),
    "logistics_share": (("emp_work_H", "emp_work_total"), "Jobs in SNI H ÷ all jobs here"),
    "logistics_lq": (
        ("emp_work_H", "emp_work_total"),
        "Share of jobs in SNI H here ÷ the same share for Sweden",
    ),
    "logistics_growth": (("emp_work_H",), "Change in SNI H jobs per year"),
    "industrial_share": (
        ("emp_work_BC", "emp_work_F", "emp_work_total"),
        "Jobs in manufacturing, mining and construction ÷ all jobs",
    ),
    "job_hub": (("commuters_in", "commuters_out"), "In-commuters ÷ out-commuters"),
    "jobs_ratio": (("emp_work_total", "emp_res_total"), "Jobs here ÷ employed residents"),
    "grp_growth_5y": (("grp",), "Change in GRP per year over five years, current prices"),
    "industrial_cluster": (
        ("business_area_employees", "pop"),
        "Employees in SCB's business areas per 1,000 residents",
    ),
    "industrial_land": (("industrial_land",), ""),
    "industrial_assessed_per_unit": (("industrial_assessed_per_unit",), ""),
    "industrial_kt": (("industrial_kt",), "Purchase price ÷ assessed value, county"),
    "warehouse_kt": (("warehouse_kt",), "Purchase price ÷ assessed value, county"),
    "net_income_mean": (("net_income_mean",), ""),
    "income_rel": (("income_median",), "Median income ÷ Sweden's"),
    "income_rel_kommun": (("net_income_mean",), "Mean net income ÷ the municipality's"),
    "low_econ_std": (("low_econ_std",), ""),
    "emp_rate": (("emp_rate",), ""),
    "sei": (("sei",), "SCB's socio-economic index; higher means more challenges"),
    "sei_improvement": (("sei",), "Index ten years ago minus the index now"),
    "area_type": (("area_type",), "SCB's area type, 1 (most challenges) to 5"),
    "dom_net_pct": (("dom_net_pct",), ""),
}
SCORE_TIP = (
    "Headroom location score, 0–100, computed from the public statistics on this page.\n"
    "Weights: config/weights.yaml. Method: the Method page."
)


@functools.lru_cache(maxsize=4096)
def tip(key: str, level: str, period: str | None = None, note: str = "") -> str:
    """Text for the hover box: where the figure comes from."""
    spec = FEAT_SRC.get(key)
    if not spec:
        return note
    inds, how = spec
    lines, seen, as_of = [], set(), ""
    for name in inds:
        s = D.source(name, level)
        if not s or s["source_table"] in seen:
            continue
        seen.add(s["source_table"])
        lines.append(f"{s['source']}, {s['source_table']} (latest {s['period']})")
        as_of = max(as_of, str(s.get("as_of") or "")[:10])
    if not lines:
        return note
    out = ["Source: " + lines[0], *("        " + x for x in lines[1:])]
    if period:
        out.append(f"Figure: {period}")
    if how:
        out.append(how)
    if note:
        out.append(note)
    if as_of:
        out.append(f"Retrieved {as_of}")
    return "\n".join(out)


def sv(html: str, key: str, level: str, period=None, note: str = "") -> str:
    """Wrap a formatted figure so hovering it shows its source."""
    t = tip(key, level, str(period) if period else None, note)
    return f'<span data-src="{ui.esc(t)}">{html}</span>' if t and html not in ("", "–") else html


def score_cell(v: float | None) -> str:
    txt = score_txt(v)
    return f'<span data-src="{ui.esc(SCORE_TIP)}">{txt}</span>' if v is not None else txt


if not D.available:
    ui.hero("Locations", "Location statistics have not been built yet.")
    ui.note("Run <code>uv run headroom locations</code> to fetch them from SCB and Kolada.")
    st.stop()

NAT = D.feat("00")
qp_level, qp_code = st.query_params.get("level"), st.query_params.get("code")
place = D.place(qp_code) if qp_code else None
if place is not None and (
    (qp_level and place["level"] != qp_level) or place["level"] not in ("lan", "kommun", "regso")
):
    place = None


# ============================================================================ start

LEVELS = {"Län": "lan", "Kommun": "kommun", "RegSO": "regso"}
LEVEL_PLURAL = {"lan": "län", "kommun": "kommuner", "regso": "RegSO areas"}
SIZES = {
    "lan": {"Any size": 0, "250,000+": 250_000, "500,000+": 500_000, "1,000,000+": 1_000_000},
    "kommun": {
        "Any size": 0,
        "10,000+": 10_000,
        "25,000+": 25_000,
        "50,000+": 50_000,
        "100,000+": 100_000,
    },
    "regso": {"Any size": 0, "1,000+": 1_000, "2,000+": 2_000, "3,000+": 3_000, "5,000+": 5_000},
}
ALL_COUNTIES, ALL_KOMMUNER, ALL_GROUPS = "All län", "All kommuner", "All groups"
SELECTED = HIGH  # selected areas light up in the theme's orange
SELECTED_LINE = "#8A3A07"


_FRAMES: dict[str, pl.DataFrame] = {}  # this run's frames; the page script reruns from the top


def level_frame(level: str) -> pl.DataFrame:
    """One row per place at a level, with its names, features and scores."""
    if level not in _FRAMES:
        names = dict(zip(D.loc["code"], D.loc["name"], strict=True))
        _FRAMES[level] = (
            D.loc.filter(pl.col("level") == level)
            .select("code", "name", "parent_lan", "parent_kommun", "kommungrupp")
            .join(D.feats.drop("level"), on="code", how="left")
            .join(D.scores.select("code", "residential", "logistics"), on="code", how="left")
            .with_columns(
                pl.col("parent_lan").replace_strict(names, default=None).alias("county"),
                pl.col("parent_kommun").replace_strict(names, default=None).alias("kommun"),
            )
        )
    return _FRAMES[level]


def area_label(level: str, r: dict) -> str:
    return f"{r['name']} · {r['kommun']}" if level == "regso" and r.get("kommun") else r["name"]


def start_page() -> None:
    state = st.session_state
    # a click on a municipality in the RegSO map: becomes the Kommun filter before it is drawn
    if "_loc_pick_kommun" in state:
        state["loc_kommun"] = state.pop("_loc_pick_kommun")

    left, right = st.columns([1, 2], gap="large")
    with left:
        go_to(place_search(index_for_session(), placeholder="Search län, kommun or area"))
        level = LEVELS[st.selectbox("Level", list(LEVELS), index=1, key="loc_level")]
        df = level_frame(level)
        lans = level_frame("lan").sort("code")
        kommuner = level_frame("kommun")
        counties = lans["name"].to_list()
        sizes = SIZES[level]
        county = state.get("loc_county", ALL_COUNTIES)
        if county not in (ALL_COUNTIES, *counties):
            county = state["loc_county"] = ALL_COUNTIES
        in_county = (
            kommuner if county == ALL_COUNTIES else kommuner.filter(pl.col("county") == county)
        )
        knames = sorted(in_county["name"].to_list())
        kommun = state.get("loc_kommun", ALL_KOMMUNER)
        if kommun not in (ALL_KOMMUNER, *knames):
            kommun = state["loc_kommun"] = ALL_KOMMUNER
        kommun_code = dict(zip(kommuner["name"], kommuner["code"], strict=True)).get(kommun)
        group = state.get("loc_group", ALL_GROUPS)
        size = state.get(f"loc_size_{level}", "Any size")

        def apply(frame: pl.DataFrame, skip: str = "") -> pl.DataFrame:
            if level != "lan" and county != ALL_COUNTIES and skip != "county":
                frame = frame.filter(pl.col("county") == county)
            if level == "regso" and kommun_code and skip != "kommun":
                frame = frame.filter(pl.col("parent_kommun") == kommun_code)
            if level == "kommun" and group != ALL_GROUPS and skip != "group":
                frame = frame.filter(pl.col("kommungrupp") == group)
            if sizes.get(size) and skip != "size":
                frame = frame.filter(pl.col("population") >= sizes[size])
            return frame

        passing = apply(df)
        options = passing.sort("name")
        labels = {r["code"]: area_label(level, r) for r in options.to_dicts()}
        key = f"loc_areas_{level}"
        if key in state:  # keep picks that the other filters still allow
            state[key] = [c for c in state[key] if c in labels]
        selected = st.multiselect(
            {"lan": "Län", "kommun": "Kommun", "regso": "RegSO"}[level],
            list(labels),
            format_func=lambda c: labels.get(c, c),
            key=key,
            placeholder=f"Choose {LEVEL_PLURAL[level]}",
        )

        styles: list[str] = []

        def grey(key: str, options: list, have: set, offset: int = 1) -> None:
            for i, o in enumerate(options):
                if o not in have:
                    styles.append(
                        f'body:has(.st-key-{key} input:focus) [role="option"][data-key="{i + offset}"] {{ color: var(--muted) !important; }}'
                    )

        if level != "lan":
            st.selectbox("Län", [ALL_COUNTIES, *counties], key="loc_county")
            grey("loc_county", counties, set(apply(df, "county")["county"].drop_nulls()))
        if level == "regso":
            st.selectbox("Kommun", [ALL_KOMMUNER, *knames], key="loc_kommun")
        if level == "kommun":
            groups = sorted(df["kommungrupp"].drop_nulls().unique().to_list())
            st.selectbox("Municipality group (SKR)", [ALL_GROUPS, *groups], key="loc_group")
            grey("loc_group", groups, set(apply(df, "group")["kommungrupp"].drop_nulls()))
        st.selectbox("Population", list(sizes), key=f"loc_size_{level}")
        have = {
            s
            for s, v in sizes.items()
            if apply(df, "size").filter(pl.col("population") >= v).height
        }
        grey(f"loc_size_{level}", list(sizes), have, 0)
        if styles:
            ui.render("<style>" + "\n".join(styles) + "</style>")
        if selected:
            links = "".join(f"<li>{link(level, c, labels.get(c, c))}</li>" for c in selected[:12])
            more = (
                f'<li class="more">and {len(selected) - 12} more in the table below</li>'
                if len(selected) > 12
                else ""
            )
            ui.render(
                f'<div class="hr-loc-open"><div class="hr-eyebrow">Open</div><ul>{links}{more}</ul></div>'
            )

    with right:
        start_map(level, df, passing, selected, county, kommun_code)

    shown = passing.filter(pl.col("code").is_in(selected)) if selected else passing
    ranking_table(level, shown)


def _bbox_of(features: list[dict]) -> tuple[list[float], list[float]]:
    lats, lons = [], []
    for ft in features:
        _bbox(ft["geometry"]["coordinates"], lats, lons)
    return lats, lons


def fit_view(lats: list[float], lons: list[float], height: int, width: int = 780):
    """Centre and zoom that fit a bounding box in a web-mercator map of this size."""
    if not lats:
        return dict(lat=62.6, lon=16.8), 4.4
    y = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))  # noqa: E731
    y0, y1 = y(min(lats)), y(max(lats))
    dy = max(y1 - y0, 1e-4)
    dx = max(math.radians(max(lons) - min(lons)), 1e-4)
    zh = math.log2(height * 0.85 * 2 * math.pi / (512 * dy))  # MapLibre: 512 px tiles
    zw = math.log2(width * 0.85 * 2 * math.pi / (512 * dx))
    zoom = max(2.8, min(12.5, min(zh, zw)))
    lat = math.degrees(2 * math.atan(math.exp((y0 + y1) / 2)) - math.pi / 2)
    return dict(lat=lat, lon=(min(lons) + max(lons)) / 2), zoom


def _hover(r: dict) -> str:
    parts = [f"<b>{ui.esc(r['name'])}</b>"]
    if r.get("residential") is not None:
        parts.append(f"Residential {r['residential']:.0f}")
    if r.get("logistics") is not None:
        parts.append(f"Logistics {r['logistics']:.0f}")
    if r.get("population") is not None:
        parts.append(f"{r['population']:,.0f} residents")
    return "<br>".join(parts)


def start_map(
    level: str,
    df: pl.DataFrame,
    passing: pl.DataFrame,
    selected: list[str],
    county: str,
    kommun_code: str | None,
) -> None:
    """Sweden, a county or a municipality's RegSO; the places that pass the filters in light
    blue, the chosen ones lit up in orange. A click opens the place."""
    height = 720
    pick_kommun = False
    if level == "regso":
        if kommun_code:
            kommuner = [kommun_code]
        elif selected:
            kommuner = sorted({c[:4] for c in selected})
        elif county != ALL_COUNTIES:
            kommuner = sorted(passing["parent_kommun"].drop_nulls().unique().to_list())
        else:
            kommuner = []
        feats = []
        for k in kommuner:
            gj_k = geojson(f"regso/{k}")
            feats += gj_k["features"] if gj_k else []
        if not feats:  # no municipality chosen yet: pick one on the map of Sweden
            pick_kommun = True
            level_geo, frame = "kommun", level_frame("kommun")
            gj = geojson("kommun")
            passing_codes, selected = set(frame["code"]), []
        else:
            level_geo, frame = "regso", df.filter(pl.col("parent_kommun").is_in(kommuner))
            gj = {"type": "FeatureCollection", "features": feats}
            passing_codes = set(passing["code"])
    else:
        level_geo, frame, gj = level, df, geojson(level)
        passing_codes = set(passing["code"])
    if gj is None:
        ui.note("Map geometry is missing from the snapshot.")
        return

    rows = frame.select("code", "name", "residential", "logistics", "population").to_dicts()
    base = [r for r in rows if r["code"] not in selected]
    lit = [r for r in rows if r["code"] in selected]
    fig = go.Figure()
    fig.add_trace(
        go.Choroplethmap(
            geojson=gj,
            featureidkey="properties.code",
            locations=[r["code"] for r in base],
            z=[1 if r["code"] in passing_codes else 0 for r in base],
            zmin=0,
            zmax=1,
            colorscale=[[0, "#ECECEC"], [0.5, "#ECECEC"], [0.5, "#BFDDE4"], [1, "#BFDDE4"]],
            showscale=False,
            marker_line_color="#FFFFFF",
            marker_line_width=0.7 if level_geo != "regso" else 0.9,
            marker_opacity=0.9,
            customdata=[_hover(r) for r in base],
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    if lit:
        fig.add_trace(
            go.Choroplethmap(
                geojson=gj,
                featureidkey="properties.code",
                locations=[r["code"] for r in lit],
                z=[1] * len(lit),
                colorscale=[[0, SELECTED], [1, SELECTED]],
                showscale=False,
                marker_line_color=SELECTED_LINE,
                marker_line_width=2.2,
                marker_opacity=0.8,
                customdata=[_hover(r) for r in lit],
                hovertemplate="%{customdata}<extra></extra>",
            )
        )
    by_code = {ft["properties"]["code"]: ft for ft in gj["features"]}
    if selected:
        focus = [by_code[c] for c in selected if c in by_code]
    elif len(passing_codes & set(by_code)) < len(by_code):
        focus = [by_code[c] for c in passing_codes if c in by_code]
    else:
        focus = list(by_code.values())
    center, zoom = fit_view(*_bbox_of(focus), height=height)
    fig.update_layout(
        map=dict(style="carto-positron", zoom=zoom, center=center),
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        hovermode="closest",
    )
    ev = st.plotly_chart(
        fig,
        config=CONFIG,  # the page scrolls; the filters zoom
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key=f"map_start_{level}_{level_geo}_{kommun_code}",
    )
    pts = (
        (ev or {}).get("selection", {}).get("points", [])
        if isinstance(ev, dict)
        else getattr(getattr(ev, "selection", None), "points", [])
    )
    code = pts[0].get("location") if pts else None
    if code and pick_kommun:
        st.session_state["_loc_pick_kommun"] = D.name(code)
        st.rerun()
    elif code:
        go_to(f"{level_geo}:{code}")
    hint = (
        "Choose a kommun (or click one) to draw its RegSO areas. "
        if pick_kommun
        else "Orange: your choice. Light blue: matches the filters. Click a place to open it. "
    )
    ui.source_line(
        hint
        + "Borders: SCB öppna geodata (RegSO 2025, joined to kommuner and län). Basemap © OpenStreetMap contributors, © CARTO."
    )


def ranking_table(level: str, f: pl.DataFrame) -> None:
    noun = LEVEL_PLURAL[level]
    ui.section(
        None,
        noun[0].upper() + noun[1:],
        "Highest residential score first. Hover a figure for its source; click a name to open it.",
    )
    f = f.sort("residential", descending=True, nulls_last=True)
    cap = 400
    rows = []
    for i, r in enumerate(f.head(cap).to_dicts(), start=1):
        row = {"rank": i, "res": score_cell(r["residential"])}
        pop = sv(ui.fmt_num(r["population"]), "population", level, r.get("population_year"))
        if level == "regso":
            flags = ""
            if r.get("utsatt"):
                lab = "Särskilt utsatt" if r["utsatt"] == "sarskilt_utsatt" else "Utsatt"
                flags = ui.flag(
                    lab,
                    f"Polisen, Lägesbild över utsatta områden 2025: {r['utsatt_name']}. The police's borders do not follow RegSO; this link is approximate.",
                    "high",
                )
            row |= {
                "name": link("regso", r["code"], r["name"])
                + f'<span class="sub">{ui.esc(r.get("kommun") or "")}</span>',
                "pop": pop,
                "g": sv(ui.fmt_delta(r["growth"]), "growth", level, r.get("growth_basis")),
                "y": sv(ui.fmt_pct(r["share_20_34"], 0), "share_20_34", level),
                "rent": sv(
                    ui.fmt_pct(r["rental_share"], 0),
                    "rental_share",
                    level,
                    r.get("rental_share_period"),
                ),
                "inc": sv(ui.fmt_x(r["income_rel_kommun"], 2), "income_rel_kommun", level),
                "sei": sv(
                    ui.fmt_num(num(r.get("sei_improvement")), 1),
                    "sei_improvement",
                    level,
                    r.get("sei_improvement_period"),
                ),
                "flags": flags,
            }
        else:
            row |= {
                "log": score_cell(r["logistics"]),
                "name": link(level, r["code"], r["name"])
                + (
                    f'<span class="sub">{ui.esc(r["county"] or "")}</span>'
                    if level == "kommun"
                    else ""
                ),
                "pop": pop,
                "g5": sv(ui.fmt_delta(r["growth_5y"]), "growth_5y", level),
                "hp": sv(
                    ui.fmt_num(num(r["housing_pressure"]), 1),
                    "housing_pressure",
                    level,
                    r.get("housing_pressure_period"),
                ),
                "bme": sv(bme_text(r["bme"], level == "kommun"), "bme", level, r.get("bme_period")),
                "lq": sv(ui.fmt_pct(r["logistics_share"], 1), "logistics_share", level),
                "c100": sv(ui.fmt_people(r["catchment_100km"]), "catchment_100km", level),
            }
        rows.append(row)
    if level == "regso":
        cols = [
            ui.Col("rank", "", "dim"),
            ui.Col("res", "Residential", "big", raw_html=True),
            ui.Col("name", "Area", "name", raw_html=True),
            ui.Col("pop", "Population", "num", raw_html=True),
            ui.Col("g", "Growth / yr", "num", raw_html=True),
            ui.Col("y", "Aged 20–34", "num", raw_html=True),
            ui.Col("rent", "Rental share", "num", raw_html=True),
            ui.Col("inc", "Income vs kommun", "num", raw_html=True),
            ui.Col("sei", "SEI improvement, 10y", "num", raw_html=True),
            ui.Col("flags", "", "html"),
        ]
    else:
        cols = [
            ui.Col("rank", "", "dim"),
            ui.Col("res", "Residential", "big", raw_html=True),
            ui.Col("log", "Logistics", "big", raw_html=True),
            ui.Col("name", "Län" if level == "lan" else "Kommun", "name", raw_html=True),
            ui.Col("pop", "Population", "num", raw_html=True),
            ui.Col("g5", "Growth / yr, 5y", "num", raw_html=True),
            ui.Col("hp", "Residents / new home", "num", raw_html=True),
            ui.Col("bme", "Housing market", raw_html=True),
            ui.Col("lq", "Logistics jobs", "num", raw_html=True),
            ui.Col("c100", "Within 100 km", "num", raw_html=True),
        ]
    ui.table(rows, cols, max_height=620)
    shown = f"{min(f.height, cap):,} of {f.height:,}" if f.height > cap else f"{f.height:,}"
    src_line(
        ["pop", "completions_q", "bme", "emp_work_H", "catchment_100km"]
        if level != "regso"
        else ["pop", "dwellings_rental", "net_income_mean", "sei"],
        f"{shown} {noun}. Scores 0–100, method on the Method page."
        + (" Narrow with the filters, or export all of them." if f.height > cap else ""),
        level=level,
    )
    keep = [
        "code",
        "name",
        *(["kommun"] if level == "regso" else []),
        *(["county"] if level != "lan" else []),
        *(["kommungrupp"] if level == "kommun" else []),
        "population",
        "residential",
        *(["logistics"] if level != "regso" else []),
        *(
            ["growth", "share_20_34", "rental_share", "income_rel_kommun", "sei_improvement"]
            if level == "regso"
            else [
                "growth_5y",
                "housing_pressure",
                "young_inflow",
                "bme",
                "logistics_share",
                "catchment_100km",
            ]
        ),
    ]
    export = f.select(keep).with_columns(
        pl.lit(D.meta.get("as_of", {}).get("generated", "")).alias("as_of")
    )
    st.write("")
    b1, b2, _ = st.columns([1.2, 1.2, 8])
    b1.download_button(
        "Export CSV",
        export.write_csv().encode("utf-8"),
        file_name=f"headroom_locations_{level}.csv",
        mime="text/csv",
    )
    buf = io.BytesIO()
    export.write_excel(buf, worksheet="Locations", autofit=True)
    b2.download_button(
        "Export Excel",
        buf.getvalue(),
        file_name=f"headroom_locations_{level}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ============================================================================ place pages


def compare_rows(
    level: str, code: str, items: list[tuple[str, str, callable, str]], peers: list[str]
) -> list[dict]:
    """Indicator | place | county | Sweden | similar (mean) | percentile."""
    f = D.feat(code)
    lan = D.feat(code[:2]) if level == "kommun" else {}
    peer_f = [D.feat(p) for p in peers]
    n_level = {"lan": "counties", "kommun": "municipalities", "regso": "areas"}.get(level, "")
    pct_tip = ui.esc("Share of Sweden's " + n_level + " with a lower value")
    rows = []
    for key, label, fmt, per_key in items:
        x = f.get(key)
        pv = [p.get(key) for p in peer_f if p.get(key) is not None]
        pr = None if key in NO_PERCENTILE else pct_rank(level, key, x)
        per = f.get(per_key) if per_key else None
        rows.append(
            {
                "k": label + (f'<span class="sub">{ui.esc(str(per))}</span>' if per else ""),
                "v": sv(fmt(x), key, level, per),
                "lan": sv(fmt(lan.get(key)), key, "lan", lan.get(per_key) if per_key else None)
                if lan
                else "",
                "nat": sv(fmt(NAT.get(key)), key, "riket", NAT.get(per_key) if per_key else None),
                "peer": sv(
                    fmt(sum(pv) / len(pv)),
                    key,
                    "kommun",
                    note=f"Mean of {len(pv)} similar municipalities (Kolada's peer group)",
                )
                if pv
                else "–",
                "pct": "–" if pr is None else f'<span data-src="{pct_tip}">{pr:.0f}</span>',
            }
        )
    return rows


def compare_table(
    rows: list[dict], level: str, place_label: str, with_lan: bool = True, with_peer: bool = True
) -> None:
    cols = [ui.Col("k", "Indicator", "html"), ui.Col("v", place_label, "num", raw_html=True)]
    if with_lan:
        cols.append(ui.Col("lan", "County", "num", raw_html=True))
    cols.append(ui.Col("nat", "Sweden", "num", raw_html=True))
    if with_peer:
        cols.append(ui.Col("peer", "Similar municipalities", "num", raw_html=True))
    cols.append(ui.Col("pct", "Percentile", "num", raw_html=True))
    ui.table(rows, cols)


def so_what(items: list[tuple[str, str]]) -> None:
    """Short rule-based reading: (state, sentence)."""
    if not items:
        return
    lis = "".join(f"<li>{ui.marker(s)}{ui.esc(t)}</li>" for s, t in items)
    ui.render(f'<ul class="hr-sowhat">{lis}</ul>')


def kolada_peers(code: str) -> list[str]:
    p = D.peers.filter((pl.col("code") == code) & (pl.col("peer_set") == "kolada"))
    return p.sort("rank")["peer_code"].to_list()


def residential_reading(name: str, f: dict) -> list[tuple[str, str]]:
    out = []
    hp, nhp = f.get("housing_pressure"), NAT.get("housing_pressure")
    bme = f.get("bme")
    g5 = f.get("growth_5y")
    if g5 is not None and g5 < 0:
        out.append(
            (
                "high",
                f"The population is shrinking ({ui.fmt_delta(g5)} a year over five years). Rental demand rests on turnover, not growth; vacancy is the risk to price.",
            )
        )
    if hp is not None and nhp is not None and hp > nhp and bme == 0:
        out.append(
            (
                "ok",
                f"{name} adds {hp:.1f} residents per new home against {nhp:.1f} for Sweden, and reports a housing shortage: demand for rental flats should hold, and new projects meet less competing supply.",
            )
        )
    elif bme == 2:
        out.append(
            (
                "high",
                f"{name} reports a housing surplus: expect longer letting times and pressure on rents in weaker stock.",
            )
        )
    elif hp is not None and nhp is not None and hp < nhp and g5 is not None and g5 > 0:
        out.append(
            (
                "watch",
                f"Building has kept pace with growth ({hp:.1f} residents per new home, Sweden {nhp:.1f}): rent growth depends on location within {name}, not on scarcity.",
            )
        )
    yi = f.get("young_inflow")
    if yi is not None and yi > 2:
        out.append(
            (
                "ok",
                f"Young adults move in: {yi:.1f} more 20–34-year-olds per 1,000 residents arrive from the rest of Sweden each year than leave. That is the renter cohort.",
            )
        )
    elif yi is not None and yi < -3:
        out.append(
            (
                "watch",
                f"Young adults move out ({yi:.1f} per 1,000 residents a year, net, to the rest of Sweden): rental demand relies on other groups.",
            )
        )
    pipe = f.get("pipeline")
    k = level_feats("kommun")
    p75 = k["pipeline"].quantile(0.75) if "pipeline" in k.columns else None
    if pipe is not None and p75 is not None and pipe > p75:
        out.append(
            (
                "watch",
                f"Many homes are under way ({pipe:.1f} starts per 1,000 residents over the last four quarters): new supply over the next two years could press lettings and rents.",
            )
        )
    return out


def logistics_reading(name: str, f: dict) -> list[tuple[str, str]]:
    out = []
    c100 = f.get("catchment_100km")
    if c100 is not None and c100 >= 2_000_000:
        out.append(
            (
                "ok",
                f"{ui.fmt_people(c100)} people live within 100 km: a last-mile market where land near people is scarce.",
            )
        )
    elif c100 is not None and c100 < 500_000:
        out.append(
            (
                "watch",
                f"Only {ui.fmt_people(c100)} people within 100 km: logistics here serves a region or a through-route, not a large consumer market.",
            )
        )
    lq = f.get("logistics_lq")
    if lq is not None and lq >= 1.3:
        out.append(
            (
                "ok",
                f"Transport and warehousing are a local specialty ({lq:.1f} times Sweden's share of jobs): an existing labour pool and tenant base.",
            )
        )
    nd = f.get("node_distance")
    if nd is not None and nd <= 20:
        which = min(
            (
                (f.get(f"dist_{k}"), f.get(f"dist_{k}_name"))
                for k in ("port", "terminal", "airport")
                if f.get(f"dist_{k}") is not None
            ),
            key=lambda t: t[0],
        )
        out.append(("ok", f"{which[1]} is {which[0]:.0f} km away (straight line)."))
    hub = f.get("job_hub")
    if hub is not None and hub >= 1.1:
        out.append(
            (
                "ok",
                f"More people commute in than out ({hub:.2f} to 1): {name} is a regional job hub.",
            )
        )
    return out


def residential_section(level: str, code: str, name: str) -> None:
    f = D.feat(code)
    with st.container(key="band_tint_res"):
        ui.section(None, "Residential", "What the numbers mean for owning rental housing here.")
        so_what(residential_reading(name, f))
        peers = kolada_peers(code) if level == "kommun" else []
        items = [
            ("population", "Population", ui.fmt_num, "population_year"),
            ("growth_1y", "Growth, last year", ui.fmt_delta, "population_year"),
            ("growth_5y", "Growth per year, 5 years", ui.fmt_delta, None),
            ("growth_10y", "Growth per year, 10 years", ui.fmt_delta, None),
            (
                "housing_pressure",
                "New residents per completed home",
                lambda v: ui.fmt_num(v, 1),
                "housing_pressure_period",
            ),
            (
                "household_pressure",
                "New households per completed home",
                lambda v: ui.fmt_num(v, 2),
                "housing_pressure_period",
            ),
            (
                "pipeline",
                "Homes started per 1,000 residents, 4 quarters",
                lambda v: ui.fmt_num(v, 1),
                "pipeline_period",
            ),
            (
                "young_inflow",
                "Net inflow 20–34 from rest of Sweden, per 1,000",
                lambda v: ui.fmt_num(v, 1),
                "young_inflow_period",
            ),
            (
                "int_net_per_1000",
                "Net immigration per 1,000",
                lambda v: ui.fmt_num(v, 1),
                "int_net_per_1000_period",
            ),
            ("share_20_34", "Share aged 20–34", lambda v: ui.fmt_pct(v, 1), "population_year"),
            (
                "bme",
                "Housing market (municipality's own assessment)",
                lambda v: bme_text(v, level == "kommun"),
                "bme_period",
            ),
            (
                "rental_share",
                "Rental flats, share of dwellings",
                lambda v: ui.fmt_pct(v, 0),
                "rental_share_period",
            ),
            (
                "rental_share_new",
                "Rental share of homes completed, 5 years",
                lambda v: ui.fmt_pct(v, 0),
                None,
            ),
            ("rent_sqm", "Median rent, SEK per sq m and year", ui.fmt_num, "rent_sqm_period"),
            (
                "transit_500m",
                "Homes within 500 m of public transport",
                lambda v: ui.fmt_num(v, 0) + "%" if v is not None else "–",
                "transit_500m_period",
            ),
            ("income_median", "Median income, SEK thousands", ui.fmt_num, "income_median_period"),
            (
                "education",
                "Share 25–65 with 3+ years' higher education",
                lambda v: ui.fmt_pct(v, 0),
                "education_period",
            ),
            (
                "unemployment",
                "Open unemployment, 18–65",
                lambda v: ui.fmt_num(v, 1) + "%" if v is not None else "–",
                "unemployment_period",
            ),
            ("tax_base_pct", "Tax base, % of Sweden", ui.fmt_num, "tax_base_pct_period"),
        ]
        if level == "lan":
            items = [i for i in items if i[0] not in ("rent_sqm",)]
        compare_table(
            compare_rows(level, code, items, peers),
            level,
            name,
            with_lan=level == "kommun",
            with_peer=bool(peers),
        )
        src_line(
            [
                "pop",
                "households",
                "completions_q",
                "starts_q",
                "dom_net_20_34",
                "bme",
                "dwellings_rental",
                "rent_sqm",
                "transit_500m",
                "income_median",
                "edu_post3",
                "unemployment",
                "tax_base_pct",
            ],
            "Percentile: share of Sweden's municipalities (counties, for a county) with a lower value. Similar municipalities: mean of Kolada's peer group. "
            "Hover a figure for its source.",
            level=level,
        )
        if f.get("rent_sqm") is None and level == "kommun":
            ui.note(
                "SCB publishes rents per municipality only for the 20 largest municipalities (TAB4603), so the rent row is empty here."
            )
        presumption_note(level, code)
        st.write("")
        c1, _, c2 = st.columns([1, 0.08, 1])
        with c1:
            ui.render(ui.eyebrow("Population, index 2015 = 100"))
            pop_index_chart(level, code, name)
        with c2:
            ui.render(ui.eyebrow("Homes started and completed vs population change"))
            construction_chart(code)
        src_line(["pop", "starts_q", "completions_q"], level=level)


def presumption_note(level: str, code: str) -> None:
    """New-build rent premium: SCB publishes it for the three metro regions and two size
    groups only."""
    p = D.place(code)
    if not p or level != "kommun":
        return
    reg = p.get("storstad")
    if not reg:
        pop = (D.feat(code) or {}).get("population")
        if pop is None:
            return
        reg = "0040" if pop > 75_000 else "0041"
    names = {
        "0010": "Stor-Stockholm",
        "0020": "Stor-Göteborg",
        "0030": "Stor-Malmö",
        "0040": "municipalities above 75,000 residents outside the metro regions",
        "0041": "municipalities below 75,000 residents outside the metro regions",
    }
    pre = D.series("newbuild_rent_presumption", [reg]).tail(1)
    neg = D.series("newbuild_rent_negotiated", [reg]).tail(1)
    if pre.height and neg.height and pre["period"][0] == neg["period"][0]:
        a, b = pre["value"][0], neg["value"][0]
        ui.note(
            f"New-build rents in {names.get(reg, reg)}, {pre['period'][0]}: presumption rent SEK {a:,.0f} per sq m a year, "
            f"negotiated utility-value rent SEK {b:,.0f}: a premium of {ui.fmt_pct(a / b - 1)}. "
            f"Source: SCB {ui.source_link(D.source('newbuild_rent_presumption').get('source_url'), 'TAB6417')}."
        )


def pop_index_chart(level: str, code: str, name: str) -> None:
    lines = [(code, name, INK, 2.2)]
    if level in ("kommun", "regso"):
        k = code[:4]
        if level == "regso":
            lines.append((k, D.name(k), BLUE, 1.6))
        else:
            lines.append((code[:2], D.name(code[:2]), BLUE, 1.6))
    elif level == "lan":
        pass
    lines.append(("00", "Sweden", MUTED, 1.6))
    fig = go.Figure()
    base_year = "2015"
    for c, label, color, width in lines:
        s = D.series("pop", [c]).filter(pl.col("period").str.len_chars() == 4)
        s = s.filter(pl.col("period") >= base_year)
        if s.is_empty() or s["period"][0] != base_year:
            continue
        b = s["value"][0]
        fig.add_scatter(
            x=s["period"].to_list(),
            y=(s["value"] / b * 100).to_list(),
            mode="lines",
            name=label,
            line=dict(color=color, width=width),
            hovertemplate=f"{ui.esc(label)} %{{x}}: %{{y:.1f}}<extra></extra>",
        )
    fig.add_hline(y=100, line=dict(color=RULE, width=1))
    fig.update_layout(
        height=300,
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    if not fig.data:
        ui.note("No unbroken population series from 2015 for this place.")
        return
    st.plotly_chart(fig, config=CONFIG, width="stretch")


def construction_chart(code: str) -> None:
    starts = L.annual(
        L.as_map(L.series(D.ind.filter(pl.col("code") == code), "starts_q")).get(code, {})
    )
    comps = L.annual(
        L.as_map(L.series(D.ind.filter(pl.col("code") == code), "completions_q")).get(code, {})
    )
    pop = L.as_map(L.series(D.ind.filter(pl.col("code") == code), "pop")).get(code, {})
    years = [y for y in sorted(starts) if y >= "2015"]
    if not years:
        ui.note("No construction statistics for this place.")
        return
    dpop = [
        pop.get(y, None) - pop.get(str(int(y) - 1))
        if pop.get(y) is not None and pop.get(str(int(y) - 1)) is not None
        else None
        for y in years
    ]
    fig = go.Figure()
    fig.add_bar(
        x=years,
        y=[starts.get(y) for y in years],
        name="Started",
        marker_color=LIGHT_BLUE,
        hovertemplate="Started %{x}: %{y:,.0f}<extra></extra>",
    )
    fig.add_bar(
        x=years,
        y=[comps.get(y) for y in years],
        name="Completed",
        marker_color=BLUE,
        hovertemplate="Completed %{x}: %{y:,.0f}<extra></extra>",
    )
    fig.add_scatter(
        x=years,
        y=dpop,
        name="Population change",
        mode="lines+markers",
        line=dict(color=INK, width=1.6),
        marker=dict(size=5, symbol="square"),
        hovertemplate="Population change %{x}: %{y:,.0f}<extra></extra>",
    )
    fig.update_layout(
        height=300,
        barmode="group",
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(tickformat=","),
    )
    st.plotly_chart(fig, config=CONFIG, width="stretch")


def logistics_section(level: str, code: str, name: str) -> None:
    f = D.feat(code)
    ui.section(
        None,
        "Logistics and light industrial",
        "People within reach, the nodes nearby, and the local logistics labour market.",
    )
    so_what(logistics_reading(name, f))
    km = lambda v: ui.fmt_km(v)  # noqa: E731
    items = [
        ("catchment_50km", "People within 50 km", ui.fmt_people, None),
        ("catchment_100km", "People within 100 km", ui.fmt_people, None),
        ("catchment_200km", "People within 200 km", ui.fmt_people, None),
        ("dist_port", "Nearest port", km, "dist_port_name"),
        ("dist_terminal", "Nearest intermodal terminal", km, "dist_terminal_name"),
        ("dist_airport", "Nearest cargo airport", km, "dist_airport_name"),
        ("dist_motorway", "Nearest junction, E4/E6/E18/E20", km, "dist_motorway_name"),
    ]
    if level != "regso":
        items += [
            (
                "logistics_jobs",
                "Jobs in transport and warehousing (SNI H)",
                ui.fmt_num,
                "jobs_period",
            ),
            (
                "logistics_share",
                "Share of local jobs in SNI H",
                lambda v: ui.fmt_pct(v, 1),
                "jobs_period",
            ),
            ("logistics_lq", "SNI H share vs Sweden", lambda v: ui.fmt_x(v, 2), None),
            (
                "logistics_growth",
                "SNI H job growth per year",
                ui.fmt_delta,
                "logistics_growth_period",
            ),
            (
                "industrial_share",
                "Share of jobs in manufacturing and construction",
                lambda v: ui.fmt_pct(v, 0),
                "jobs_period",
            ),
            (
                "job_hub",
                "In-commuters per out-commuter",
                lambda v: ui.fmt_num(v, 2),
                "job_hub_period",
            ),
            ("jobs_ratio", "Jobs per employed resident", lambda v: ui.fmt_num(v, 2), "jobs_period"),
            (
                "grp_growth_5y",
                "GRP growth per year, 5 years (current prices)",
                ui.fmt_delta,
                "grp_period",
            ),
            (
                "industrial_cluster",
                "Jobs in business areas per 1,000 residents (2020)",
                ui.fmt_num,
                None,
            ),
            (
                "industrial_land",
                "Ground area of industrial buildings, 1,000 sq m",
                ui.fmt_num,
                "industrial_land_period",
            ),
            (
                "industrial_assessed_per_unit",
                "Assessed value per industrial unit, SEK k",
                ui.fmt_num,
                "industrial_assessed_period",
            ),
            (
                "industrial_kt",
                "Industrial sales, price / assessed value (county)",
                lambda v: ui.fmt_x(v, 2),
                "industrial_kt_period",
            ),
            (
                "warehouse_kt",
                "Warehouse sales, price / assessed value (county)",
                lambda v: ui.fmt_x(v, 2),
                "warehouse_kt_period",
            ),
        ]
    peers = kolada_peers(code) if level == "kommun" else []
    rows = compare_rows(level if level != "regso" else "regso", code, items, peers)
    # nearest-node rows: show the node's name under the distance
    compare_table(rows, level, name, with_lan=level == "kommun", with_peer=bool(peers))
    src_line(
        [
            "catchment_100km",
            "dist_port",
            "dist_motorway",
            "emp_work_H",
            "commuters_in",
            "grp",
            "business_area_employees",
            "industrial_land",
            "industrial_assessed_per_unit",
            "industrial_kt",
        ],
        "Distances and catchments are straight lines from the population-weighted centre, an approximation of drive time. "
        "Ports, terminals and airports: config/geo/logistics_nodes.yaml (Wikidata, OpenStreetMap); the terminal list is not complete.",
        level=level,
    )
    if f.get("dist_motorway") is None:
        ui.note(
            "Motorway junctions are not in this snapshot yet (config/geo/motorway_junctions.csv is built from OpenStreetMap by scripts/motorway_junctions.py), so that component is left out of the score."
        )
    rk = L.ranking_for(level, code)
    if rk:
        txt = "; ".join(
            f"{ui.esc(r['name'])}, number {r['rank']} in {ui.esc(r['publisher'])}'s {ui.esc(r['title'])} ({ui.source_link(r['source_url'], str(r['year']))})"
            for r in rk
        )
        ui.note(f"Logistics rankings: {txt}. Shown for context; not part of the score.")


def areas_section(level: str, code: str, name: str, highlight: str | None = None) -> None:
    """RegSO within a municipality (or the municipalities within a county): table and map."""
    if level == "lan":
        sub = D.loc.filter((pl.col("level") == "kommun") & (pl.col("parent_lan") == code))
        ui.section(None, "Municipalities", f"The municipalities in {name}. Click one to open it.")
        df = (
            sub.select("code", "name")
            .join(D.feats, on="code", how="left")
            .join(D.scores.select("code", "residential", "logistics"), on="code", how="left")
            .sort("residential", descending=True, nulls_last=True)
        )
        rows = [
            {
                "res": score_cell(r["residential"]),
                "log": score_cell(r["logistics"]),
                "name": link("kommun", r["code"], r["name"]),
                "pop": sv(
                    ui.fmt_num(r["population"]), "population", "kommun", r.get("population_year")
                ),
                "g5": sv(ui.fmt_delta(r["growth_5y"]), "growth_5y", "kommun"),
                "hp": sv(
                    ui.fmt_num(num(r["housing_pressure"]), 1),
                    "housing_pressure",
                    "kommun",
                    r.get("housing_pressure_period"),
                ),
                "bme": sv(bme_text(r["bme"]), "bme", "kommun", r.get("bme_period")),
            }
            for r in df.to_dicts()
        ]
        ui.table(
            rows,
            [
                ui.Col("res", "Residential", "big", raw_html=True),
                ui.Col("log", "Logistics", "big", raw_html=True),
                ui.Col("name", "Municipality", "name", raw_html=True),
                ui.Col("pop", "Population", "num", raw_html=True),
                ui.Col("g5", "Growth / yr, 5y", "num", raw_html=True),
                ui.Col("hp", "Residents per new home", "num", raw_html=True),
                ui.Col("bme", "Housing market", raw_html=True),
            ],
        )
        src_line(["pop", "completions_q", "bme"], level="kommun")
        county_map(code)
        return
    kommun = code[:4]
    sub = D.loc.filter((pl.col("level") == "regso") & (pl.col("parent_kommun") == kommun))
    title = "Areas" if level != "regso" else f"Other areas in {D.name(kommun)}"
    desc = (
        f"SCB's regional statistical areas (RegSO) in {name}."
        if level == "kommun"
        else f"Where {name} sits among the areas of {D.name(kommun)}."
    ) + " Click an area to open it."
    ui.section(None, title, desc)
    df = (
        sub.select("code", "name")
        .join(D.feats, on="code", how="left")
        .join(D.scores.select("code", "residential"), on="code", how="left")
    )
    df = df.sort("residential", descending=True, nulls_last=True)
    region_map(kommun, df, highlight)
    rows = []
    for r in df.to_dicts():
        flags = ""
        if r.get("utsatt"):
            lab = "Särskilt utsatt" if r["utsatt"] == "sarskilt_utsatt" else "Utsatt"
            flags += ui.flag(
                lab,
                f"Polisen, Lägesbild över utsatta områden 2025: {r['utsatt_name']} ({'särskilt utsatt område' if r['utsatt'] == 'sarskilt_utsatt' else 'utsatt område'}, trend {r['utsatt_trend']}). The police's borders do not follow RegSO; this link is approximate.",
                "high",
            )
        if (
            r.get("area_type_then") is not None
            and r["area_type_then"] <= 2
            and (r.get("sei_improvement") or 0) >= 1.0
        ):
            flags += ui.flag(
                "Improving",
                "In one of SCB's two weakest socio-economic area types ten years ago, and the index has improved by at least one point since: a value-add signal.",
                "neutral",
            )
        if r.get("series_break"):
            flags += ui.flag(
                "New borders",
                "SCB redrew this area in RegSO 2025; growth before 2024 is not comparable and is left out.",
                "neutral",
            )
        at = r.get("area_type")
        sub_txt = f"Area type {int(at)}" if at is not None else ""
        cls = "me" if r["code"] == highlight else None
        rows.append(
            {
                "_class": cls,
                "res": score_cell(r["residential"]),
                "name": link("regso", r["code"], r["name"])
                + f'<span class="sub">{ui.esc(sub_txt)}</span>',
                "pop": sv(
                    ui.fmt_num(r["population"]), "population", "regso", r.get("population_year")
                ),
                "g": sv(ui.fmt_delta(r["growth"]), "growth", "regso", r.get("growth_basis"))
                + (
                    f'<span class="sub">{ui.esc(r.get("growth_basis") or "")}</span>'
                    if r.get("growth_basis")
                    else ""
                ),
                "y": sv(ui.fmt_pct(r["share_20_34"], 0), "share_20_34", "regso"),
                "rent": sv(
                    ui.fmt_pct(r["rental_share"], 0),
                    "rental_share",
                    "regso",
                    r.get("rental_share_period"),
                ),
                "inc": sv(ui.fmt_x(r["income_rel_kommun"], 2), "income_rel_kommun", "regso"),
                "sei": sv(
                    ui.fmt_num(num(r.get("sei_improvement")), 1),
                    "sei_improvement",
                    "regso",
                    r.get("sei_improvement_period"),
                ),
                "flags": flags,
            }
        )
    ui.table(
        rows,
        [
            ui.Col("res", "Residential", "big", raw_html=True),
            ui.Col("name", "Area", "name", raw_html=True),
            ui.Col("pop", "Population", "num", raw_html=True),
            ui.Col("g", "Growth / yr", "num", raw_html=True),
            ui.Col("y", "Aged 20–34", "num", raw_html=True),
            ui.Col("rent", "Rental share", "num", raw_html=True),
            ui.Col("inc", "Income vs municipality", "num", raw_html=True),
            ui.Col("sei", "SEI improvement, 10y", "num", raw_html=True),
            ui.Col("flags", "", "html"),
        ],
        max_height=640,
    )
    src_line(
        ["pop", "dwellings_rental", "net_income_mean", "sei"],
        "Growth per year since 2020 where the area kept its borders, otherwise last year. SEI improvement: fall in SCB's socio-economic "
        "index (higher index = more challenges), percentage points. Flags: Polisen, Lägesbild över utsatta områden 2025.",
        level="regso",
    )


def region_map(kommun: str, df: pl.DataFrame, highlight: str | None) -> None:
    """A municipality's RegSO by residential score. The open area is filled orange with a
    dark outline; police-listed vulnerable areas get a thick orange outline and a light
    orange fill."""
    gj = geojson(f"regso/{kommun}")
    if gj is None:
        ui.note("Area geometry is missing for this municipality.")
        return
    lats, lons = _bbox_of(gj["features"])
    # police-listed and open areas are drawn in orange only, so the blue does not muddy it
    lit = {r["code"] for r in df.to_dicts() if r.get("utsatt")} | {highlight}
    base = df.filter(~pl.col("code").is_in(list(lit - {None})))
    fig = go.Figure()
    fig.add_trace(
        go.Choroplethmap(
            geojson=gj,
            featureidkey="properties.code",
            locations=base["code"].to_list(),
            z=base["residential"].to_list(),
            zmin=0,
            zmax=100,
            colorscale=BLUE_RAMP,
            marker_line_color="#FFFFFF",
            marker_line_width=0.8,
            marker_opacity=0.82,
            customdata=base["name"].to_list(),
            hovertemplate="%{customdata}: %{z:.0f}<extra></extra>",
            colorbar=dict(
                thickness=8,
                len=0.45,
                x=0.01,
                xanchor="left",
                outlinewidth=0,
                tickfont=dict(size=11, color=GRAPHITE),
                title=dict(text="Residential", font=dict(size=11, color=GRAPHITE)),
            ),
        )
    )
    ut = [r for r in df.to_dicts() if r.get("utsatt") and r["code"] != highlight]
    if ut:
        fig.add_trace(
            go.Choroplethmap(
                geojson=gj,
                featureidkey="properties.code",
                locations=[r["code"] for r in ut],
                z=[1] * len(ut),
                colorscale=[[0, "rgba(225,110,29,0.42)"], [1, "rgba(225,110,29,0.42)"]],
                showscale=False,
                marker_line_color=HIGH,
                marker_line_width=3.5,
                customdata=[
                    f"{ui.esc(r['name'])}: {score_txt(r['residential'])}"
                    f"<br>Police-listed: {ui.esc(r.get('utsatt_name') or '')}"
                    for r in ut
                ],
                hovertemplate="%{customdata}<extra></extra>",
            )
        )
    if highlight:
        fig.add_trace(
            go.Choroplethmap(
                geojson=gj,
                featureidkey="properties.code",
                locations=[highlight],
                z=[1],
                colorscale=[[0, HIGH], [1, HIGH]],
                showscale=False,
                marker_opacity=0.88,
                marker_line_color=INK,
                marker_line_width=3.5,
                customdata=[
                    f"{ui.esc(D.name(highlight))}: {score_txt(D.score(highlight, 'residential'))}"
                ],
                hovertemplate="%{customdata}<extra></extra>",
            )
        )
    center, zoom = fit_view(lats, lons, height=600, width=1150)
    zoom += 0.5  # RegSO run out over the sea; the land deserves the room
    fig.update_layout(
        map=dict(style="carto-positron", zoom=zoom, center=center),
        height=600,
        margin=dict(l=0, r=0, t=0, b=0),
        hovermode="closest",
    )
    ev = st.plotly_chart(
        fig,
        config={**CONFIG, "scrollZoom": True},
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key=f"map_r_{kommun}_{highlight}",
    )
    pts = (
        ev.get("selection", {}).get("points", [])
        if isinstance(ev, dict)
        else getattr(getattr(ev, "selection", None), "points", [])
    )
    for p in pts:
        loc_code = p.get("location")
        if loc_code and loc_code != highlight:
            go_to(f"regso:{loc_code}")
    ui.source_line(
        ("Solid orange: this area. " if highlight else "")
        + "Orange outline: police-listed vulnerable area (approximate). "
        "Borders: SCB öppna geodata, RegSO 2025 (simplified). Basemap © OpenStreetMap contributors, © CARTO."
    )


def _bbox(coords, lats, lons) -> None:
    if coords and isinstance(coords[0], (int, float)):
        lons.append(coords[0])
        lats.append(coords[1])
        return
    for c in coords:
        _bbox(c, lats, lons)


def county_map(lan: str) -> None:
    gj = geojson("kommun")
    if gj is None:
        return
    feats = {
        "type": "FeatureCollection",
        "features": [f for f in gj["features"] if f["properties"]["code"][:2] == lan],
    }
    k = (
        D.loc.filter((pl.col("level") == "kommun") & (pl.col("parent_lan") == lan))
        .select("code", "name")
        .join(D.scores.select("code", "residential"), on="code", how="left")
    )
    fig = go.Figure(
        go.Choropleth(
            geojson=feats,
            featureidkey="properties.code",
            locations=k["code"].to_list(),
            z=k["residential"].to_list(),
            zmin=0,
            zmax=100,
            colorscale=BLUE_RAMP,
            marker_line_color="#FFFFFF",
            marker_line_width=0.6,
            customdata=k["name"].to_list(),
            hovertemplate="%{customdata}: %{z:.0f}<extra></extra>",
            colorbar=dict(
                thickness=8,
                len=0.5,
                x=0.01,
                xanchor="left",
                outlinewidth=0,
                title=dict(text="Residential", font=dict(size=11, color=GRAPHITE)),
            ),
        )
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(
        height=520, margin=dict(l=0, r=0, t=0, b=0), hovermode="closest", dragmode=False
    )
    ev = st.plotly_chart(
        fig,
        config=CONFIG,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key=f"map_l_{lan}",
    )
    pts = (
        ev.get("selection", {}).get("points", [])
        if isinstance(ev, dict)
        else getattr(getattr(ev, "selection", None), "points", [])
    )
    if pts and pts[0].get("location"):
        go_to(f"kommun:{pts[0]['location']}")


def peers_section(level: str, code: str, name: str) -> None:
    ui.section(
        None,
        "Peers",
        "The place against its neighbours, similar places and Sweden, on the same rows.",
    )
    rows_spec: list[tuple[str, str, str]] = [(code, "This place", "")]
    if level in ("kommun", "lan"):
        sets = {"kolada": "Similar (Kolada)", "nearest": "Neighbour", "twin": "Statistical twin"}
        p = D.peers.filter(pl.col("code") == code).sort("peer_set", "rank")
        seen = {code}
        for ps in ("kolada", "nearest", "twin"):
            for r in p.filter(pl.col("peer_set") == ps).to_dicts():
                if r["peer_code"] in seen:
                    continue
                seen.add(r["peer_code"])
                reason = ""
                if ps == "nearest" and r["distance"] is not None:
                    reason = f"{r['distance']:.0f} km" + (
                        " · same labour market"
                        if "same local labour market" in (r["reason"] or "")
                        else ""
                    )
                rows_spec.append((r["peer_code"], sets[ps], reason))
        if level == "kommun":
            rows_spec.append((code[:2], "County", ""))
    elif level == "regso":
        k = code[:4]
        rows_spec.append((k, "Municipality", ""))
    rows_spec.append(("00", "", ""))
    res_key = "growth" if level == "regso" else "growth_5y"
    out = []
    for c, kind, reason in rows_spec:
        p = D.place(c)
        if not p:
            continue
        f = D.feat(c)
        lvl = p["level"]
        nm = p["name"]
        name_html = link(lvl, c, nm) if lvl != "riket" and c != code else ui.esc(nm)
        sub = kind + (f" · {reason}" if reason else "")
        out.append(
            {
                "_class": "me" if c == code else None,
                "res": score_cell(D.score(c, "residential")) if lvl != "riket" else "",
                "log": (score_cell(D.score(c, "logistics")) if lvl != "riket" else "")
                if level != "regso"
                else "",
                "name": name_html + f'<span class="sub">{ui.esc(sub)}</span>',
                "pop": sv(
                    ui.fmt_num(f.get("population")), "population", lvl, f.get("population_year")
                ),
                "g": sv(
                    ui.fmt_delta(f.get(res_key) if lvl == "regso" else f.get("growth_5y")),
                    "growth" if lvl == "regso" else "growth_5y",
                    lvl,
                ),
                "hp": ui.fmt_num(num(f.get("housing_pressure")), 1)
                if level != "regso"
                else ui.fmt_pct(f.get("rental_share"), 0),
                "y": ui.fmt_pct(f.get("share_20_34"), 1),
                "inc": ui.fmt_x(f.get("income_rel"), 2)
                if level != "regso"
                else ui.fmt_x(f.get("income_rel_kommun"), 2),
                "lq": ui.fmt_pct(f.get("logistics_share"), 1)
                if level != "regso"
                else ui.fmt_people(f.get("catchment_100km")),
            }
        )
    cols = [
        ui.Col("res", "Residential", "big", raw_html=True),
        *([ui.Col("log", "Logistics", "big", raw_html=True)] if level != "regso" else []),
        ui.Col("name", "Place", "name", raw_html=True),
        ui.Col("pop", "Population", "num", raw_html=True),
        ui.Col("g", "Growth / yr, 5y", "num", raw_html=True),
        ui.Col("hp", "Residents per new home" if level != "regso" else "Rental share", "num"),
        ui.Col("y", "Aged 20–34", "num"),
        ui.Col("inc", "Income vs Sweden" if level != "regso" else "Income vs municipality", "num"),
        ui.Col("lq", "Logistics jobs" if level != "regso" else "People within 100 km", "num"),
    ]
    ui.table(out, cols)
    src_line(
        ["pop", "completions_q", "income_median", "emp_work_H"],
        "Similar: Kolada's group 'Liknande kommuner socioekonomi'. Neighbours: nearest by population-weighted centre, same local labour market "
        "(SCB LA 2018) first. Statistical twins: nearest on standardised log population, five-year growth, median income, rental share and logistics share.",
    )


def companies_section(level: str, code: str, name: str) -> None:
    d = get_data()
    ui.section(
        None,
        "Property companies here",
        "Companies with a large share of their portfolio here, and their motivated-seller score.",
    )
    k = pl.read_csv(
        geo_ref.GEO / "kommuner.csv",
        schema_overrides={"kommun_code": pl.Utf8, "county_code": pl.Utf8},
    )
    kcode = dict(zip(k["kommun"], k["kommun_code"], strict=True))
    lan_name = dict(zip(k["county_code"], k["county"], strict=True))
    kommun = code[:4] if level in ("kommun", "regso") else None
    lan = code[:2]
    rows = []
    for r in d.scores.to_dicts():
        share = 0.0
        try:
            mix = json.loads(r.get("region_mix") or "{}")
        except ValueError:
            mix = {}
        for region, s in mix.items():
            county, kname = geo_ref._place(region)
            kc = kcode.get(kname or "")
            if (kommun and kc == kommun) or (not kommun and county == lan_name.get(lan)):
                share += float(s or 0)
        basis = f"{share:.0%} of portfolio" if share else None
        if not share:
            if kommun and r.get("city") and kcode.get(r["city"]) == kommun:
                basis = "Registered or main city"
            elif not kommun and r.get("county") == lan_name.get(lan):
                basis = "Registered or main county"
        if basis and (share >= 0.15 or not share):
            rows.append({**r, "_share": share, "_basis": basis})
    rows.sort(key=lambda r: (-r["_share"], -(r.get("score") or -1)))
    if d.fictional:
        ui.note(
            "<b>Demo mode.</b> These companies are fictional; switch to live data for real issuers."
        )
    if not rows:
        ui.note(
            f"No company in the {'demo' if d.fictional else 'live'} universe has its main portfolio or address in {ui.esc(name)}."
        )
    else:
        bands = d.cfg["motivated_seller"]["bands"]
        ui.table(
            [
                {
                    "score": (
                        ui.marker(ui.band(r["score"], bands["high"], bands["watch"]))
                        if r.get("score") is not None
                        else ""
                    )
                    + score_txt(r.get("score")),
                    "name": f'<a href="issuer?org={r["org_nr"]}" target="_self">{ui.esc(r["name"])}</a><span class="sub">{ui.esc(r.get("city") or "")}</span>',
                    "basis": r["_basis"],
                    "opp": ui.esc(d.cards[r["org_nr"]].opportunity)
                    if r["org_nr"] in d.cards
                    else "",
                }
                for r in rows[:25]
            ],
            [
                ui.Col("score", "Seller score", "big", raw_html=True),
                ui.Col("name", "Company", "name", raw_html=True),
                ui.Col("basis", "Why here"),
                ui.Col("opp", "Likely transaction"),
            ],
        )
    county = lan_name.get(lan, "")
    city = D.name(kommun) if kommun else None
    href = f"companies?county={quote(county)}" + (f"&city={quote(city)}" if city else "")
    ui.render(f'<p class="hr-note"><a href="{href}" target="_self">See in Companies</a></p>')
    ui.source_line(
        f"Portfolio split from company reports; city and county as on the Companies page. {d.source_label('company')}."
    )


def place_page(p: dict) -> None:
    level, code = p["level"], p["code"]
    name = p["name"]
    f = D.feat(code)
    if level == "lan":
        kicker = "County"
    elif level == "kommun":
        kicker = f"Municipality · {D.name(code[:2])}"
    else:
        kicker = f"Area · {D.name(code[:4])} municipality"
    nat = dict(NAT)
    for key in ("growth_1y", "housing_pressure", "young_inflow", "logistics_lq", "growth_5y"):
        if key in D.feats.columns:
            vals = level_feats("kommun")[key].drop_nulls()
            nat[f"_sd_{key}"] = float(vals.std()) if vals.len() > 1 else None
    if level == "regso":
        headline = regso_headline(name, f, D.feat(code[:4]))
    else:
        headline = L.headline(name, f, nat)
    res = D.score(code, "residential")
    stats = [
        ui.Stat(
            score_txt(res),
            "/100" if res is not None else "",
            "Residential score",
            tip=SCORE_TIP if res is not None else "",
        )
    ]
    if level != "regso":
        lg = D.score(code, "logistics")
        stats.append(
            ui.Stat(
                score_txt(lg),
                "/100" if lg is not None else "",
                "Logistics score",
                ink=True,
                tip=SCORE_TIP if lg is not None else "",
            )
        )
    stats.append(
        ui.Stat(
            ui.fmt_num(f.get("population")),
            "",
            f"Residents, {ui.esc(str(f.get('population_year') or ''))}",
            ink=True,
            tip=tip("population", level, str(f.get("population_year") or "") or None),
        )
    )
    ui.hero(kicker, ui.esc(headline), "", stats)
    go_to(place_search(index_for_session(), key="place_search_top"))
    if level == "regso":
        ui.facts(
            [
                (
                    "Population",
                    sv(
                        ui.fmt_num(f.get("population")),
                        "population",
                        level,
                        f.get("population_year"),
                    )
                    + period_txt(f.get("population_year")),
                ),
                (
                    "Growth per year",
                    sv(ui.fmt_delta(f.get("growth")), "growth", level, f.get("growth_basis"))
                    + period_txt(f.get("growth_basis")),
                ),
                (
                    "Rental share",
                    sv(
                        ui.fmt_pct(f.get("rental_share"), 0),
                        "rental_share",
                        level,
                        f.get("rental_share_period"),
                    )
                    + period_txt(f.get("rental_share_period")),
                ),
                (
                    "Area type (1–5)",
                    sv(
                        ui.fmt_num(f.get("area_type")) if f.get("area_type") is not None else "–",
                        "area_type",
                        level,
                        f.get("area_type_period"),
                    )
                    + period_txt(f.get("area_type_period")),
                ),
            ]
        )
        regso_section(code, name, f)
        areas_section("regso", code, name, highlight=code)
        logistics_section("regso", code, name)
        peers_section("regso", code, name)
        companies_section("regso", code, name)
        return
    py = f.get("population_year")
    g_per = f"{int(py) - 5}–{py}" if py else None
    ui.facts(
        [
            (
                "Population",
                sv(ui.fmt_num(f.get("population")), "population", level, py) + period_txt(py),
            ),
            (
                "Growth per year, 5 years",
                sv(ui.fmt_delta(f.get("growth_5y")), "growth_5y", level, g_per) + period_txt(g_per),
            ),
            (
                "Housing market",
                sv(
                    bme_text(f.get("bme"), level == "kommun"),
                    "bme",
                    level,
                    f.get("bme_period"),
                    "" if level == "kommun" else "Mean of the county's municipalities",
                )
                + period_txt(f.get("bme_period")),
            ),
            (
                "Median income",
                sv(
                    f"SEK {f['income_median']:,.0f}k"
                    if f.get("income_median") is not None
                    else "–",
                    "income_median",
                    level,
                    f.get("income_median_period"),
                )
                + period_txt(f.get("income_median_period")),
            ),
        ]
    )
    residential_section(level, code, name)
    logistics_section(level, code, name)
    areas_section(level, code, name)
    peers_section(level, code, name)
    companies_section(level, code, name)


def regso_headline(name: str, f: dict, k: dict) -> str:
    cands = []
    g, gk = f.get("growth"), k.get("growth_5y")
    if g is not None and gk is not None and f.get("growth_basis", "").startswith("2020"):
        word = "grown" if g >= 0 else "shrunk"
        cands.append(
            (
                abs(g - gk) / 0.015,
                f"{name} has {word} {ui.fmt_pct(abs(g), 1)} a year since 2020; the municipality {ui.fmt_pct(gk, 1)}.",
            )
        )
    si = f.get("sei_improvement")
    if (
        si is not None
        and f.get("area_type_then") is not None
        and f["area_type_then"] <= 2
        and si >= 1
    ):
        cands.append(
            (
                si / 1.5 + 0.5,
                f"{name} is improving from a weak base: SCB's socio-economic index fell {si:.1f} points in ten years.",
            )
        )
    elif si is not None and si <= -1.5:
        cands.append(
            (
                abs(si) / 1.5,
                f"{name} is weakening: SCB's socio-economic index rose {abs(si):.1f} points in ten years.",
            )
        )
    rs = f.get("rental_share")
    rk = k.get("rental_share")
    if rs is not None and rk is not None:
        cands.append(
            (
                abs(rs - rk) / 0.2,
                f"{ui.fmt_pct(rs)} of homes in {name} are rented, against {ui.fmt_pct(rk)} across the municipality.",
            )
        )
    y = f.get("share_20_34")
    yk = k.get("share_20_34")
    if y is not None and yk is not None:
        cands.append(
            (
                abs(y - yk) / 0.06,
                f"{ui.fmt_pct(y)} of residents in {name} are aged 20–34; the municipality {ui.fmt_pct(yk)}.",
            )
        )
    if not cands:
        return f"{name} has {ui.fmt_num(f.get('population'))} residents."
    return max(cands)[1]


REGSO_PCT_TIP = ui.esc("Share of Sweden's RegSO areas with a lower value")


def regso_section(code: str, name: str, f: dict) -> None:
    k = code[:4]
    kf = D.feat(k)
    with st.container(key="band_tint_res"):
        ui.section(
            None,
            "Residential",
            "The area against its municipality and Sweden. Percentile among Sweden's 3,363 areas.",
        )
        out = []
        if f.get("utsatt"):
            lab = (
                "a särskilt utsatt område"
                if f["utsatt"] == "sarskilt_utsatt"
                else "an utsatt område"
            )
            out.append(
                (
                    "high",
                    f"The police list {f['utsatt_name']} as {lab} (Lägesbild 2025, trend {f['utsatt_trend']}). The police's borders do not follow RegSO, so the link is approximate.",
                )
            )
        si = f.get("sei_improvement")
        if (
            si is not None
            and f.get("area_type_then") is not None
            and f["area_type_then"] <= 2
            and si >= 1
        ):
            out.append(
                (
                    "ok",
                    f"Improving from a weak base (area type {int(f['area_type_then'])} ten years ago, index down {si:.1f} points): the kind of area where value-add buys in before the market reprices.",
                )
            )
        if f.get("rental_share") is not None and f["rental_share"] >= 0.5:
            out.append(
                (
                    "ok",
                    f"Mostly rental ({ui.fmt_pct(f['rental_share'])} of homes): an investable stock of rental flats.",
                )
            )
        if f.get("growth") is not None and f["growth"] < -0.005:
            out.append(("high", f"The population is falling ({ui.fmt_delta(f['growth'])} a year)."))
        if f.get("series_break"):
            out.append(
                (
                    "watch",
                    "SCB redrew this area for RegSO 2025, so figures before 2024 are not comparable and long trends are left out.",
                )
            )
        so_what(out)

        def r(key, label, fmt, per=None):
            x = f.get(key)
            pr = pct_rank("regso", key, x)
            p = f.get(per) if per else None
            return {
                "k": label + (f'<span class="sub">{ui.esc(str(p))}</span>' if p else ""),
                "v": sv(fmt(x), key, "regso", p),
                "lan": sv(fmt(kf.get(key)), key, "kommun", kf.get(per) if per else None),
                "nat": sv(fmt(NAT.get(key)), key, "riket", NAT.get(per) if per else None),
                "pct": "–" if pr is None else f'<span data-src="{REGSO_PCT_TIP}">{pr:.0f}</span>',
            }

        rows = [
            r("population", "Population", ui.fmt_num, "population_year"),
            r("growth", "Growth per year", ui.fmt_delta, "growth_basis"),
            r("share_20_34", "Share aged 20–34", lambda v: ui.fmt_pct(v, 1), "population_year"),
            r(
                "rental_share",
                "Rental flats, share of dwellings",
                lambda v: ui.fmt_pct(v, 0),
                "rental_share_period",
            ),
            r(
                "condo_share",
                "Tenant-owned flats (bostadsrätt)",
                lambda v: ui.fmt_pct(v, 0),
                "rental_share_period",
            ),
            r(
                "net_income_mean",
                "Mean net income, SEK thousands",
                ui.fmt_num,
                "net_income_mean_period",
            ),
            r(
                "low_econ_std",
                "Low economic standard, %",
                lambda v: ui.fmt_num(v, 1),
                "low_econ_std_period",
            ),
            r("emp_rate", "Employment rate, 20–64", lambda v: ui.fmt_pct(v, 0), "emp_rate_period"),
            r(
                "education",
                "Share 25–65 with 3+ years' higher education",
                lambda v: ui.fmt_pct(v, 0),
            ),
            r(
                "sei",
                "Socio-economic index (higher = more challenges)",
                lambda v: ui.fmt_num(v, 1),
                "sei_period",
            ),
            r(
                "sei_improvement",
                "Index improvement, 10 years",
                lambda v: ui.fmt_num(v, 1),
                "sei_improvement_period",
            ),
            r(
                "dom_net_pct",
                "Domestic net migration, % a year",
                lambda v: ui.fmt_num(v, 1),
                "dom_net_pct_period",
            ),
        ]
        ui.table(
            rows,
            [
                ui.Col("k", "Indicator", "html"),
                ui.Col("v", name, "num", raw_html=True),
                ui.Col("lan", D.name(k), "num", raw_html=True),
                ui.Col("nat", "Sweden", "num", raw_html=True),
                ui.Col("pct", "Percentile", "num", raw_html=True),
            ],
        )
        src_line(
            [
                "pop",
                "dwellings_rental",
                "net_income_mean",
                "low_econ_std",
                "emp_rate",
                "edu_post3",
                "sei",
                "dom_net_pct",
            ],
            "– means SCB suppresses the figure for a small area or does not publish it at this level. Hover a figure for its source.",
            level="regso",
        )
        comps = D.components(code, "residential")
        if comps:
            ui.render(ui.eyebrow("Why the score"))
            ui.table(
                [
                    {
                        "c": c["label"],
                        "w": f"{c['weight']:.0%}",
                        "s": f"{c['score']:.0f}{ui.bar(c['score'] / 100)}"
                        if c["score"] is not None
                        else "–",
                    }
                    for c in comps
                ],
                [
                    ui.Col("c", "Component"),
                    ui.Col("w", "Weight", "num"),
                    ui.Col("s", "Score", "num", raw_html=True),
                ],
            )
        st.write("")
        c1, _, c2 = st.columns([1, 0.08, 1])
        with c1:
            ui.render(ui.eyebrow("Population, index 2015 = 100"))
            if f.get("series_break"):
                ui.note("Borders changed in 2024; no comparable series.")
            else:
                pop_index_chart("regso", code, name)
        with c2:
            ui.render(ui.eyebrow("Socio-economic index (lower is better)"))
            sei_chart(code, k)
        src_line(["pop", "sei"], level="regso")


def sei_chart(code: str, kommun: str) -> None:
    s = D.series("sei", [code])
    if s.is_empty():
        ui.note("No index for this area.")
        return
    fig = go.Figure()
    fig.add_scatter(
        x=s["period"].to_list(),
        y=s["value"].to_list(),
        mode="lines+markers",
        line=dict(color=INK, width=2),
        marker=dict(size=5, symbol="square"),
        name=D.name(code),
        hovertemplate="%{x}: %{y:.1f}<extra></extra>",
    )
    fig.update_layout(
        height=300,
        showlegend=False,
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, config=CONFIG, width="stretch")


# ============================================================================ run

if place is None:
    if qp_code:
        ui.note(f"No place with code {ui.esc(qp_code)} in the location data.")
    start_page()
else:
    place_page(place)
    st.write("")
    ui.render('<p class="hr-note"><a href="locations" target="_self">← All locations</a></p>')

gen = (D.meta.get("as_of") or {}).get("generated")
ui.source_line(
    f"Location statistics: SCB, Kolada, Polisen, OpenStreetMap, Wikidata; built {ui.fmt_date(gen, 'long') if gen else '–'}. "
    "Public data, the same in live and demo mode."
)
source_tips()
