"""Ranked motivated-seller screen with filters and export."""

from __future__ import annotations

import io
import json

import polars as pl
import streamlit as st

from ui import components as ui
from ui.data import get_data
from ui.labels import SEGMENT, TIER

d = get_data()
bands = d.cfg["motivated_seller"]["bands"]
min_conf = d.cfg["extraction"]["min_confidence"]

latest = (
    d.t["financials"]
    .filter(pl.col("period_end") <= d.ref)
    .sort("period_end")
    .group_by("org_nr")
    .agg(pl.all().last())
)
# field-level confidence for the two figures shown in the table
prov = d.t["provenance"].join(latest.select("org_nr", "period_end"), on=["org_nr", "period_end"])
conf = (
    prov.filter(pl.col("field").is_in(["ltv", "icr"]))
    .pivot(on="field", index="org_nr", values="confidence")
    .rename({"ltv": "conf_ltv", "icr": "conf_icr"}, strict=False)
)
df = d.scores.join(
    latest.select(
        "org_nr",
        "ltv",
        "icr",
        "property_value",
        "net_debt",
        "ebitda",
        "avg_rate",
        "debt_due_12m",
        "gross_debt",
        "period_end",
        "value_basis",
    ),
    on="org_nr",
    how="left",
).join(conf, on="org_nr", how="left")
for c in ("conf_ltv", "conf_icr"):
    if c not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))


def main_segment(mix: str | None) -> str | None:
    m = json.loads(mix or "{}")
    return max(m, key=m.get) if m else None


df = df.with_columns(
    pl.col("segment_mix").map_elements(main_segment, return_dtype=pl.Utf8).alias("main_segment"),
    pl.col("region_mix")
    .map_elements(lambda s: sorted(json.loads(s or "{}")), return_dtype=pl.List(pl.Utf8))
    .alias("regions"),
)

n_high = df.filter(pl.col("score") >= bands["high"]).height
n_watch = df.filter(pl.col("score").is_between(bands["watch"], bands["high"], closed="left")).height
n_fit = df.filter((pl.col("score") >= bands["high"]) & (pl.col("fit") >= 70)).height
n_scored = df.filter(pl.col("score").is_not_null()).height
if n_scored:
    ui.hero(
        "Screen · Motivated sellers",
        f"{n_high} of {df.height} companies show the balance sheet of a motivated seller.",
        f"{n_fit} of them also fit the strategy well. Ranked on a 0–100 score; method on the Method page.",
    )
else:
    ui.hero(
        "Screen · Motivated sellers",
        f"{df.height} Swedish property companies with listed bonds, not yet scored.",
        "Scores need reported financials, which are extracted from interim reports from milestone 3. "
        "Until then the list shows the universe, ordered by bonds maturing soonest.",
    )

# --------------------------------------------------------------------------- filters

segments = sorted({s for s in df["main_segment"].drop_nulls()}, key=lambda s: SEGMENT.get(s, s))
regions = sorted({r for rs in df["regions"].to_list() if rs for r in rs})
ui.section(1, "Ranking")
c1, c2, c3, c4, c5 = st.columns([2.6, 2.2, 2.6, 2.2, 2], gap="large")
seg_sel = c1.multiselect(
    "Segment", segments, format_func=lambda s: SEGMENT.get(s, s), placeholder="All segments"
)
tier_sel = c2.multiselect("Tier", list(TIER), format_func=TIER.get, placeholder="All tiers")
reg_sel = c3.multiselect("Region", regions, placeholder="All regions")
min_score = c4.slider("Minimum score", 0, 100, 0, step=5)
sort_by = c5.selectbox("Rank by", ["Seller score", "Strategy fit"])

f = df
if seg_sel:
    f = f.filter(pl.col("main_segment").is_in(seg_sel))
if tier_sel:
    f = f.filter(pl.col("tier").is_in(tier_sel))
if reg_sel:
    f = f.filter(pl.col("regions").list.eval(pl.element().is_in(reg_sel)).list.any())
f = f.filter(pl.col("score").fill_null(0) >= min_score)
nb = (
    d.t["bond"]
    .filter((pl.col("maturity") > d.ref) & (pl.col("maturity").dt.year() < 2100))  # skip perpetuals
    .group_by("org_nr")
    .agg(
        pl.col("maturity").min().alias("next_maturity"),
        pl.col("nominal_sek").sum().alias("bonds_sek"),
        pl.len().alias("n_bonds"),
    )
)
f = f.join(nb, on="org_nr", how="left")
if n_scored:
    f = f.sort("fit" if sort_by == "Strategy fit" else "score", descending=True, nulls_last=True)
else:
    f = f.sort("next_maturity", nulls_last=True)

# --------------------------------------------------------------------------- table

show_comp = st.toggle("Show score components", value=False)


def comp(v: float | None) -> str:
    return "–" if v is None else f"{v:.0f}"


rows = []
for i, r in enumerate(f.to_dicts(), start=1):
    state = ui.band(r["score"], bands["high"], bands["watch"])
    rows.append(
        {
            "rank": i,
            "score": f"{ui.marker(state)}{r['score']:.0f}" if r["score"] is not None else "–",
            "name": f'<a href="issuer?org={r["org_nr"]}" target="_self">{ui.esc(r["name"])}</a>'
            f'<span class="sub">{TIER[r["tier"]]} · {SEGMENT.get(r["main_segment"] or "", "–")}</span>',
            "fit": comp(r["fit"]),
            "opp": r["opportunity"],
            "s_refinancing": comp(r["s_refinancing"]),
            "s_covenant": comp(r["s_covenant"]),
            "s_leverage": comp(r["s_leverage"]),
            "s_events": comp(r["s_events"]),
            "s_market": comp(r["s_market"]),
            "hr": ui.fmt_pct(r["min_headroom_stressed"], signed=True),
            # an LTV outside 0-100% is almost always a mis-read figure: flag it as unverified
            "ltv": ui.unverified(
                ui.fmt_pct(r["ltv"]),
                0.0 if r["ltv"] is not None and not 0 <= r["ltv"] <= 1 else r["conf_ltv"],
                min_conf,
            )
            + ui.basis_tag(r["value_basis"], short=True),
            "icr": ui.unverified(ui.fmt_x(r["icr"], 1), r["conf_icr"], min_conf),
            "flags": str(r["n_flags"]) if r["n_flags"] else "",
            "nd_ebitda": ui.fmt_x(r["net_debt"] / r["ebitda"], 1)
            if r["net_debt"] is not None and r["ebitda"]
            else "–",
            "rate": ui.fmt_pct(r["avg_rate"], 1),
            "pv": ui.fmt_sek_m(r["property_value"], unit=False),
            "due12": ui.fmt_pct(r["debt_due_12m"] / r["gross_debt"])
            if r["debt_due_12m"] is not None and r["gross_debt"]
            else "–",
            "pe": ui.fmt_date(r["period_end"]),
            "next": r["next_maturity"],
            "bonds": ui.fmt_num(r["bonds_sek"]) if r["bonds_sek"] is not None else "–",
            "nb": str(r["n_bonds"] or 0),
        }
    )
key_figures = [
    ui.Col("ltv", "LTV", "num", raw_html=True),
    ui.Col("icr", "ICR", "num", raw_html=True),
    ui.Col("nd_ebitda", "Net debt / EBITDA", "num"),
    ui.Col("rate", "Avg. rate", "num"),
    ui.Col("due12", "Debt due 12m", "num"),
    ui.Col("pv", "Property value, SEK m", "num"),
    ui.Col("next", "Next bond", "date", ui.fmt_date),
]
cols = (
    [
        ui.Col("rank", "", "dim"),
        ui.Col("score", "Score", "big", raw_html=True),
        ui.Col("name", "Company", "name", raw_html=True),
        *key_figures,
    ]
    if n_scored
    else [
        ui.Col("rank", "", "dim"),
        ui.Col("name", "Company", "name", raw_html=True),
        ui.Col("next", "Next bond maturity", "date", ui.fmt_date),
        ui.Col("nb", "Bonds", "num"),
        ui.Col("bonds", "Outstanding, SEK m", "num"),
    ]
)
if show_comp and n_scored:
    cols += [
        ui.Col("fit", "Fit", "num"),
        ui.Col("s_refinancing", "Refi", "num"),
        ui.Col("s_covenant", "Cov.", "num"),
        ui.Col("s_leverage", "Lev.", "num"),
        ui.Col("s_events", "Events", "num"),
        ui.Col("s_market", "Mkt", "num"),
        ui.Col("hr", "Headroom +100bp", "num"),
        ui.Col("pe", "Figures as of", "date"),
        ui.Col("flags", "Gaps", "num"),
    ]
ui.table(rows, cols)
note = (
    "LTV is net debt over property value, at market value unless flagged BOOK (K2/K3 book "
    "value, which overstates leverage); ICR is EBIT over net interest, as reported; "
    "figures marked unverified were extracted with low confidence. "
) + (
    "Refi to Mkt are 0–100 component scores. Headroom +100bp is the tightest maintenance covenant "
    "with ICR re-tested 100bp higher. Gaps counts components without data. "
    if show_comp
    else ""
)
ui.source_line(f"{f.height} companies. {note}{d.source_label('financials')}.")

# --------------------------------------------------------------------------- export

export = f.select(
    "org_nr",
    "name",
    "tier",
    "main_segment",
    "score",
    "fit",
    "opportunity",
    "s_refinancing",
    "s_covenant",
    "s_leverage",
    "s_events",
    "s_market",
    "min_headroom",
    "min_headroom_stressed",
    "refi_ratio",
    "ltv",
    "icr",
    "property_value",
    "value_basis",
    "net_debt",
    "ebitda",
    "avg_rate",
    "debt_due_12m",
    "next_maturity",
    "period_end",
    "coverage",
    "n_flags",
).with_columns(pl.lit(d.mode).alias("data_mode"), pl.lit(str(d.ref)).alias("as_of"))
if d.fictional:
    export = export.with_columns(pl.lit("FICTIONAL DEMO DATA").alias("note"))

st.write("")
b1, b2, _ = st.columns([1.2, 1.2, 8])
b1.download_button(
    "Export CSV",
    export.write_csv().encode("utf-8"),
    file_name=f"headroom_screen_{d.ref}.csv",
    mime="text/csv",
)
buf = io.BytesIO()
export.write_excel(buf, worksheet="Screen", autofit=True)
b2.download_button(
    "Export Excel",
    buf.getvalue(),
    file_name=f"headroom_screen_{d.ref}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
