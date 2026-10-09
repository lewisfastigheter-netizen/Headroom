"""Locations: how counties, municipalities, cities and areas (RegSO) develop, read for a
value-add investor in rental housing and logistics / light industrial property.

No place selected: a statement, the place search, a ranking of municipalities and a map.
A place selected (?level=kommun&code=0380): what is happening there, how it compares with
its neighbours, similar municipalities and Sweden, what that means for housing and for
logistics, the areas inside it, and the property companies that hold most of their
portfolio there.

All figures come from data/snapshots/locations (public statistics, the same in live and
demo mode). Missing values show as –; nothing is interpolated.
"""

from __future__ import annotations

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
LEVEL_NOUN = {"lan": "county", "kommun": "municipality", "tatort": "city", "regso": "area"}


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


def src_line(indicators: list[str], extra: str = "") -> None:
    """'Source: SCB TAB6574 (2025), Kolada U30446 (2026)…' from the indicators used."""
    seen, parts = set(), []
    for name in indicators:
        s = D.source(name)
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


if not D.available:
    ui.hero("Locations", "Location statistics have not been built yet.")
    ui.note("Run <code>uv run headroom locations</code> to fetch them from SCB and Kolada.")
    st.stop()

NAT = D.feat("00")
qp_level, qp_code = st.query_params.get("level"), st.query_params.get("code")
place = D.place(qp_code) if qp_code else None
if place is not None and qp_level and place["level"] != qp_level:
    place = None


# ============================================================================ start


def start_page() -> None:
    k = level_feats("kommun")
    lc = D.cfg["location"]
    shortage = lc.get("pressure_shortage", 2.0)
    g_nat = NAT.get("growth_5y")
    both = (
        k.filter((pl.col("growth_5y") > g_nat) & (pl.col("housing_pressure") > shortage))
        if g_nat is not None
        else k.head(0)
    )
    n = both.height
    period = k["housing_pressure_period"].drop_nulls().mode().to_list()
    period = period[0] if period else ""
    improving = (
        D.feats.filter(
            (pl.col("level") == "regso")
            & (pl.col("area_type_then") <= 2)
            & (pl.col("sei_improvement") >= 1.0)
        ).height
        if "area_type_then" in D.feats.columns
        else 0
    )
    n_short = k.filter(pl.col("bme") == 0).height
    bme_year = k["bme_period"].drop_nulls().max() if "bme_period" in k.columns else ""
    ui.hero(
        "Locations",
        f"{n} of {k.height} municipalities grew faster than Sweden and built less than they grew.",
        "Population growth over five years above Sweden's, and more than "
        f"{shortage:.0f} new residents for every home completed in {period}. "
        "Where housing is scarce, rental demand holds; where land is scarce near people and roads, "
        "last-mile logistics does.",
        [
            ui.Stat(
                f"{n_short}",
                "",
                f"Municipalities that report a <b>housing shortage</b>, {ui.esc(bme_year)}",
            ),
            ui.Stat(
                f"{NAT.get('housing_pressure', 0):.1f}",
                "",
                f"New residents per completed home in <b>Sweden</b>, {ui.esc(NAT.get('housing_pressure_period') or '')}",
                ink=True,
            ),
            ui.Stat(
                f"{improving:,}",
                "",
                "Areas in SCB's two weakest socio-economic types that <b>improved</b> over ten years",
                ink=True,
            ),
        ],
    )
    go_to(place_search(index_for_session()))
    ranking_section()
    map_section()


def ranking_section() -> None:
    ui.section(
        None, "Ranking", "Every municipality on the two location scores. Click a name to open it."
    )
    loc = D.loc.filter(pl.col("level") == "kommun").select(
        "code", "name", "parent_lan", "kommungrupp"
    )
    lan_names = dict(
        zip(
            D.loc.filter(pl.col("level") == "lan")["code"],
            D.loc.filter(pl.col("level") == "lan")["name"],
            strict=True,
        )
    )
    df = (
        loc.join(
            D.feats.select(
                "code",
                "population",
                "growth_5y",
                "housing_pressure",
                "bme",
                "logistics_share",
                "catchment_100km",
                "young_inflow",
            ),
            on="code",
            how="left",
        )
        .join(D.scores.select("code", "residential", "logistics"), on="code", how="left")
        .with_columns(pl.col("parent_lan").replace_strict(lan_names, default=None).alias("county"))
    )
    counties = [lan_names[c] for c in sorted(lan_names)]
    groups = sorted(df["kommungrupp"].drop_nulls().unique().to_list())
    sizes = {
        "Any size": 0,
        "10,000+": 10_000,
        "25,000+": 25_000,
        "50,000+": 50_000,
        "100,000+": 100_000,
    }
    state = st.session_state
    county = state.get("loc_county", "All counties")
    group = state.get("loc_group", "All groups")
    size = state.get("loc_size", "Any size")
    sort = state.get("loc_sort", "Residential")

    def apply(frame: pl.DataFrame, skip: str = "") -> pl.DataFrame:
        if county != "All counties" and skip != "county":
            frame = frame.filter(pl.col("county") == county)
        if group != "All groups" and skip != "group":
            frame = frame.filter(pl.col("kommungrupp") == group)
        if sizes.get(size) and skip != "size":
            frame = frame.filter(pl.col("population") >= sizes[size])
        return frame

    styles: list[str] = []

    def grey(key: str, options: list, have: set, offset: int = 1) -> None:
        for i, o in enumerate(options):
            if o not in have:
                styles.append(
                    f'body:has(.st-key-{key} input:focus) [role="option"][data-key="{i + offset}"] {{ color: var(--muted) !important; }}'
                )

    c1, c2, c3, c4 = st.columns(4, gap="large")
    c1.selectbox("County", ["All counties", *counties], key="loc_county")
    grey("loc_county", counties, set(apply(df, "county")["county"].drop_nulls()))
    c2.selectbox("Municipality group (SKR)", ["All groups", *groups], key="loc_group")
    grey("loc_group", groups, set(apply(df, "group")["kommungrupp"].drop_nulls()))
    c3.selectbox("Population", list(sizes), key="loc_size")
    have_sizes = {
        s for s, v in sizes.items() if apply(df, "size").filter(pl.col("population") >= v).height
    }
    grey("loc_size", list(sizes), have_sizes, 0)
    c4.selectbox("Sort by", ["Residential", "Logistics"], key="loc_sort")
    if styles:
        ui.render("<style>" + "\n".join(styles) + "</style>")

    f = apply(df).sort(sort.lower(), descending=True, nulls_last=True)
    rows = []
    for i, r in enumerate(f.to_dicts(), start=1):
        rows.append(
            {
                "rank": i,
                "res": score_txt(r["residential"]),
                "log": score_txt(r["logistics"]),
                "name": link("kommun", r["code"], r["name"])
                + f'<span class="sub">{ui.esc(r["county"] or "")}</span>',
                "pop": ui.fmt_num(r["population"]),
                "g5": ui.fmt_delta(r["growth_5y"]),
                "hp": ui.fmt_num(num(r["housing_pressure"]), 1),
                "bme": bme_text(r["bme"]),
                "lq": ui.fmt_pct(r["logistics_share"], 1),
                "c100": ui.fmt_people(r["catchment_100km"]),
            }
        )
    ui.table(
        rows,
        [
            ui.Col("rank", "", "dim"),
            ui.Col("res", "Residential", "big"),
            ui.Col("log", "Logistics", "big"),
            ui.Col("name", "Municipality", "name", raw_html=True),
            ui.Col("pop", "Population", "num"),
            ui.Col("g5", "Growth / yr, 5y", "num"),
            ui.Col("hp", "Residents / new home", "num"),
            ui.Col("bme", "Housing market"),
            ui.Col("lq", "Logistics jobs", "num"),
            ui.Col("c100", "Within 100 km", "num"),
        ],
        max_height=620,
    )
    src_line(
        ["pop", "completions_q", "bme", "emp_work_H", "catchment_100km"],
        f"{f.height} municipalities. Scores 0–100, method on the Method page. Residents per new home: population change over "
        "three years divided by homes completed in the same years.",
    )
    export = f.select(
        "code",
        "name",
        "county",
        "kommungrupp",
        "population",
        "residential",
        "logistics",
        "growth_5y",
        "housing_pressure",
        "young_inflow",
        "bme",
        "logistics_share",
        "catchment_100km",
    ).with_columns(pl.lit(D.meta.get("as_of", {}).get("generated", "")).alias("as_of"))
    st.write("")
    b1, b2, _ = st.columns([1.2, 1.2, 8])
    b1.download_button(
        "Export CSV",
        export.write_csv().encode("utf-8"),
        file_name="headroom_locations.csv",
        mime="text/csv",
    )
    buf = io.BytesIO()
    export.write_excel(buf, worksheet="Locations", autofit=True)
    b2.download_button(
        "Export Excel",
        buf.getvalue(),
        file_name="headroom_locations.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def map_section() -> None:
    ui.section(None, "Map", "Municipalities by score. Click one to open it.")
    kind = st.segmented_control(
        "Score", ["Residential", "Logistics"], default="Residential", key="loc_map_kind"
    )
    kind = (kind or "Residential").lower()
    gj = geojson("kommun")
    if gj is None:
        ui.note("Map geometry is missing from the snapshot.")
        return
    k = (
        D.loc.filter(pl.col("level") == "kommun")
        .select("code", "name")
        .join(D.scores.select("code", kind), on="code", how="left")
    )
    fig = go.Figure(
        go.Choropleth(
            geojson=gj,
            featureidkey="properties.code",
            locations=k["code"].to_list(),
            z=k[kind].to_list(),
            zmin=0,
            zmax=100,
            colorscale=BLUE_RAMP,
            marker_line_color="#FFFFFF",
            marker_line_width=0.4,
            customdata=k["name"].to_list(),
            hovertemplate="%{customdata}: %{z:.0f}<extra></extra>",
            colorbar=dict(
                thickness=8,
                len=0.5,
                x=0.02,
                xanchor="left",
                outlinewidth=0,
                tickfont=dict(size=11, color=GRAPHITE),
                title=dict(text=kind.capitalize(), font=dict(size=11, color=GRAPHITE)),
            ),
        )
    )
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
    fig.update_layout(
        height=760, margin=dict(l=0, r=0, t=0, b=0), hovermode="closest", dragmode=False
    )
    ev = st.plotly_chart(
        fig,
        config=CONFIG,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key=f"map_k_{kind}",
    )
    pts = (
        (ev or {}).get("selection", {}).get("points", [])
        if isinstance(ev, dict)
        else getattr(getattr(ev, "selection", None), "points", [])
    )
    if pts:
        code = pts[0].get("location")
        if code:
            go_to(f"kommun:{code}")
    src_line(
        ["pop", "bme", "catchment_100km"],
        "Borders: SCB öppna geodata, RegSO 2025 joined to municipalities.",
    )


# ============================================================================ place pages


def compare_rows(
    level: str, code: str, items: list[tuple[str, str, callable, str]], peers: list[str]
) -> list[dict]:
    """Indicator | place | county | Sweden | similar (mean) | percentile."""
    f = D.feat(code)
    lan = D.feat(code[:2]) if level in ("kommun", "tatort") else {}
    peer_f = [D.feat(p) for p in peers]
    rows = []
    for key, label, fmt, per_key in items:
        x = f.get(key)
        pv = [p.get(key) for p in peer_f if p.get(key) is not None]
        pr = (
            None
            if key in NO_PERCENTILE
            else pct_rank(level if level != "tatort" else "kommun", key, x)
        )
        rows.append(
            {
                "k": label
                + (
                    f'<span class="sub">{ui.esc(str(f.get(per_key) or ""))}</span>'
                    if per_key and f.get(per_key)
                    else ""
                ),
                "v": fmt(x),
                "lan": fmt(lan.get(key)) if lan else "",
                "nat": fmt(NAT.get(key)),
                "peer": fmt(sum(pv) / len(pv)) if pv else "–",
                "pct": "–" if pr is None else f"{pr:.0f}",
            }
        )
    return rows


def compare_table(
    rows: list[dict], level: str, place_label: str, with_lan: bool = True, with_peer: bool = True
) -> None:
    cols = [ui.Col("k", "Indicator", "html"), ui.Col("v", place_label, "num")]
    if with_lan:
        cols.append(ui.Col("lan", "County", "num"))
    cols.append(ui.Col("nat", "Sweden", "num"))
    if with_peer:
        cols.append(ui.Col("peer", "Similar municipalities", "num"))
    cols.append(ui.Col("pct", "Percentile", "num"))
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
            "Percentile: share of Sweden's municipalities (counties, for a county) with a lower value. Similar municipalities: mean of Kolada's peer group.",
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
        src_line(["pop", "starts_q", "completions_q"])


def presumption_note(level: str, code: str) -> None:
    """New-build rent premium: SCB publishes it for the three metro regions and two size
    groups only."""
    p = D.place(code)
    if not p or level not in ("kommun", "tatort"):
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
    if level in ("kommun", "tatort", "regso"):
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


def areas_section(
    level: str, code: str, name: str, highlight: str | None = None, members: list[str] | None = None
) -> None:
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
                "res": score_txt(r["residential"]),
                "log": score_txt(r["logistics"]),
                "name": link("kommun", r["code"], r["name"]),
                "pop": ui.fmt_num(r["population"]),
                "g5": ui.fmt_delta(r["growth_5y"]),
                "hp": ui.fmt_num(num(r["housing_pressure"]), 1),
                "bme": bme_text(r["bme"]),
            }
            for r in df.to_dicts()
        ]
        ui.table(
            rows,
            [
                ui.Col("res", "Residential", "big"),
                ui.Col("log", "Logistics", "big"),
                ui.Col("name", "Municipality", "name", raw_html=True),
                ui.Col("pop", "Population", "num"),
                ui.Col("g5", "Growth / yr, 5y", "num"),
                ui.Col("hp", "Residents per new home", "num"),
                ui.Col("bme", "Housing market"),
            ],
        )
        src_line(["pop", "completions_q", "bme"])
        county_map(code)
        return
    kommun = code[:4]
    sub = D.loc.filter((pl.col("level") == "regso") & (pl.col("parent_kommun") == kommun))
    title = "Areas" if level != "regso" else f"Other areas in {D.name(kommun)}"
    desc = (
        f"SCB's regional statistical areas (RegSO) in {name}."
        if level == "kommun"
        else f"The RegSO that make up {name} are marked; the rest of {D.name(kommun)} is shown for context."
        if level == "tatort"
        else f"Where {name} sits among the areas of {D.name(kommun)}."
    ) + " Click an area to open it."
    ui.section(None, title, desc)
    df = (
        sub.select("code", "name", "tatort_codes")
        .join(D.feats, on="code", how="left")
        .join(D.scores.select("code", "residential"), on="code", how="left")
    )
    if members is not None:
        df = df.with_columns(pl.col("code").is_in(members).alias("_member"))
    df = df.sort("residential", descending=True, nulls_last=True)
    region_map(kommun, df, highlight, members)
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
        cls = "me" if r["code"] == highlight or (members is not None and r.get("_member")) else None
        rows.append(
            {
                "_class": cls,
                "res": score_txt(r["residential"]),
                "name": link("regso", r["code"], r["name"])
                + f'<span class="sub">{ui.esc(sub_txt)}</span>',
                "pop": ui.fmt_num(r["population"]),
                "g": ui.fmt_delta(r["growth"])
                + (
                    f'<span class="sub">{ui.esc(r.get("growth_basis") or "")}</span>'
                    if r.get("growth_basis")
                    else ""
                ),
                "y": ui.fmt_pct(r["share_20_34"], 0),
                "rent": ui.fmt_pct(r["rental_share"], 0),
                "inc": ui.fmt_x(r["income_rel_kommun"], 2),
                "sei": ui.fmt_num(num(r.get("sei_improvement")), 1),
                "flags": flags,
            }
        )
    ui.table(
        rows,
        [
            ui.Col("res", "Residential", "big"),
            ui.Col("name", "Area", "name", raw_html=True),
            ui.Col("pop", "Population", "num"),
            ui.Col("g", "Growth / yr", "num", raw_html=True),
            ui.Col("y", "Aged 20–34", "num"),
            ui.Col("rent", "Rental share", "num"),
            ui.Col("inc", "Income vs municipality", "num"),
            ui.Col("sei", "SEI improvement, 10y", "num"),
            ui.Col("flags", "", "html"),
        ],
        max_height=640,
    )
    src_line(
        ["pop", "dwellings_rental", "net_income_mean", "sei"],
        "Growth per year since 2020 where the area kept its borders, otherwise last year. SEI improvement: fall in SCB's socio-economic "
        "index (higher index = more challenges), percentage points. Flags: Polisen, Lägesbild över utsatta områden 2025.",
    )


def region_map(
    kommun: str, df: pl.DataFrame, highlight: str | None, members: list[str] | None
) -> None:
    gj = geojson(f"regso/{kommun}")
    if gj is None:
        ui.note("Area geometry is missing for this municipality.")
        return
    lats, lons = [], []
    focus = set(members or []) | ({highlight} if highlight else set())
    for ft in gj["features"]:
        if not members or ft["properties"]["code"] in focus:
            _bbox(ft["geometry"]["coordinates"], lats, lons)
    codes = df["code"].to_list()
    z = df["residential"].to_list()
    names = df["name"].to_list()
    fig = go.Figure()
    if members is not None:
        mem = df.filter(pl.col("_member"))
        rest = df.filter(~pl.col("_member"))
        if rest.height:
            fig.add_trace(
                go.Choroplethmap(
                    geojson=gj,
                    featureidkey="properties.code",
                    locations=rest["code"].to_list(),
                    z=[0] * rest.height,
                    colorscale=[[0, "#ECECEC"], [1, "#ECECEC"]],
                    showscale=False,
                    marker_line_color="#FFFFFF",
                    marker_line_width=0.8,
                    customdata=rest["name"].to_list(),
                    hovertemplate="%{customdata}<extra></extra>",
                    marker_opacity=0.85,
                )
            )
        codes, z, names = mem["code"].to_list(), mem["residential"].to_list(), mem["name"].to_list()
    fig.add_trace(
        go.Choroplethmap(
            geojson=gj,
            featureidkey="properties.code",
            locations=codes,
            z=z,
            zmin=0,
            zmax=100,
            colorscale=BLUE_RAMP,
            marker_line_color="#FFFFFF",
            marker_line_width=0.8,
            marker_opacity=0.82,
            customdata=names,
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
    if highlight:
        fig.add_trace(
            go.Choroplethmap(
                geojson=gj,
                featureidkey="properties.code",
                locations=[highlight],
                z=[1],
                colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
                showscale=False,
                marker_line_color=INK,
                marker_line_width=2.5,
                hoverinfo="skip",
            )
        )
    ut = [r for r in df.to_dicts() if r.get("utsatt")]
    if ut:
        fig.add_trace(
            go.Choroplethmap(
                geojson=gj,
                featureidkey="properties.code",
                locations=[r["code"] for r in ut],
                z=[1] * len(ut),
                colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
                showscale=False,
                marker_line_color=HIGH,
                marker_line_width=1.8,
                hoverinfo="skip",
            )
        )
    if lats:
        span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.5)
        zoom = max(5.0, min(13.0, math.log2(360 / max(span * 1.6, 1e-3)) - 0.2))
        center = dict(lat=(max(lats) + min(lats)) / 2, lon=(max(lons) + min(lons)) / 2)
    else:
        zoom, center = 8, dict(lat=59.3, lon=18.0)
    fig.update_layout(
        map=dict(style="carto-positron", zoom=zoom, center=center),
        height=560,
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
        "Borders: SCB öppna geodata, RegSO 2025 (simplified). Orange outline: police-listed vulnerable area (approximate). Basemap © OpenStreetMap contributors, © CARTO."
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
                "res": score_txt(D.score(c, "residential")) if lvl != "riket" else "",
                "log": (score_txt(D.score(c, "logistics")) if lvl != "riket" else "")
                if level != "regso"
                else "",
                "name": name_html + f'<span class="sub">{ui.esc(sub)}</span>',
                "pop": ui.fmt_num(f.get("population")),
                "g": ui.fmt_delta(f.get(res_key) if lvl == "regso" else f.get("growth_5y")),
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
        ui.Col("res", "Residential", "big"),
        *([ui.Col("log", "Logistics", "big")] if level != "regso" else []),
        ui.Col("name", "Place", "name", raw_html=True),
        ui.Col("pop", "Population", "num"),
        ui.Col("g", "Growth / yr, 5y", "num"),
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
    kommun = code[:4] if level in ("kommun", "tatort", "regso") else None
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
    members = None
    if level == "tatort":
        members = [c for c in (p.get("tatort_codes") or "").split(",") if c]
        stats_code = p["parent_kommun"]
    else:
        stats_code = code
    f = D.feat(stats_code)
    fc = D.feat(code)
    lan_name = D.name(code[:2]) if level != "lan" else ""
    if level == "lan":
        kicker = "County"
    elif level == "kommun":
        kicker = f"Municipality · {lan_name}"
    elif level == "tatort":
        kicker = f"City · {D.name(stats_code)} municipality"
    else:
        kicker = f"Area · {D.name(code[:4])} municipality"
    nat = dict(NAT)
    for key in ("growth_1y", "housing_pressure", "young_inflow", "logistics_lq", "growth_5y"):
        if key in D.feats.columns:
            vals = level_feats("kommun")[key].drop_nulls()
            nat[f"_sd_{key}"] = float(vals.std()) if vals.len() > 1 else None
    if level == "regso":
        headline = regso_headline(name, fc, D.feat(code[:4]))
    else:
        headline = L.headline(name if level != "tatort" else D.name(stats_code), f, nat)
    pop = fc.get("population") if level == "tatort" else f.get("population")
    pop_year = fc.get("population_year") if level == "tatort" else f.get("population_year")
    stats = [
        ui.Stat(
            score_txt(D.score(code if level == "regso" else stats_code, "residential")),
            "/100"
            if D.score(code if level == "regso" else stats_code, "residential") is not None
            else "",
            "Residential score",
        ),
    ]
    if level != "regso":
        stats.append(
            ui.Stat(
                score_txt(D.score(stats_code, "logistics")),
                "/100" if D.score(stats_code, "logistics") is not None else "",
                "Logistics score",
                ink=True,
            )
        )
    stats.append(
        ui.Stat(
            ui.fmt_num(pop),
            "",
            f"Residents, {ui.esc(str(pop_year or ''))}"
            + (" (SCB tätort statistics)" if level == "tatort" else ""),
            ink=True,
        )
    )
    intro = ""
    if level == "tatort":
        intro = f"Figures below are for {ui.esc(D.name(stats_code))} municipality; the areas that make up the city are marked under Areas."
    ui.hero(kicker, ui.esc(headline), intro, stats)
    go_to(place_search(index_for_session(), key="place_search_top"))
    if level == "regso":
        ui.facts(
            [
                (
                    "Population",
                    ui.fmt_num(fc.get("population")) + period_txt(fc.get("population_year")),
                ),
                (
                    "Growth per year",
                    ui.fmt_delta(fc.get("growth")) + period_txt(fc.get("growth_basis")),
                ),
                (
                    "Rental share",
                    ui.fmt_pct(fc.get("rental_share"), 0)
                    + period_txt(fc.get("rental_share_period")),
                ),
                (
                    "Area type (1–5)",
                    (ui.fmt_num(fc.get("area_type")) if fc.get("area_type") is not None else "–")
                    + period_txt(fc.get("area_type_period")),
                ),
            ]
        )
        regso_section(code, name, fc)
        areas_section("regso", code, name, highlight=code)
        logistics_section("regso", code, name)
        peers_section("regso", code, name)
        companies_section("regso", code, name)
        return
    ui.facts(
        [
            ("Population", ui.fmt_num(f.get("population")) + period_txt(f.get("population_year"))),
            (
                "Growth per year, 5 years",
                ui.fmt_delta(f.get("growth_5y"))
                + period_txt(
                    f"{int(f['population_year']) - 5}–{f['population_year']}"
                    if f.get("population_year")
                    else None
                ),
            ),
            (
                "Housing market",
                bme_text(f.get("bme"), level in ("kommun", "tatort"))
                + period_txt(f.get("bme_period")),
            ),
            (
                "Median income",
                (f"SEK {f['income_median']:,.0f}k" if f.get("income_median") is not None else "–")
                + period_txt(f.get("income_median_period")),
            ),
        ]
    )
    sl = "kommun" if level == "tatort" else level
    residential_section(sl, stats_code, D.name(stats_code))
    logistics_section(sl, stats_code, D.name(stats_code))
    areas_section(
        level if level != "tatort" else "tatort",
        stats_code if level != "tatort" else code,
        name,
        members=members,
    )
    peers_section(sl, stats_code, D.name(stats_code))
    companies_section(level, stats_code, name)


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
            return {
                "k": label
                + (
                    f'<span class="sub">{ui.esc(str(f.get(per)))}</span>'
                    if per and f.get(per)
                    else ""
                ),
                "v": fmt(x),
                "lan": fmt(kf.get(key)),
                "nat": fmt(NAT.get(key)),
                "pct": "–" if pr is None else f"{pr:.0f}",
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
                ui.Col("v", name, "num"),
                ui.Col("lan", D.name(k), "num"),
                ui.Col("nat", "Sweden", "num"),
                ui.Col("pct", "Percentile", "num"),
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
            "– means SCB suppresses the figure for a small area or does not publish it at this level.",
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
        src_line(["pop", "sei"])


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
