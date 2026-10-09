"""All outstanding bonds."""

from __future__ import annotations

from datetime import timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from ui import components as ui
from ui.data import get_data
from ui.labels import TIER
from ui.plotly_template import BLUE, CONFIG, INK

d = get_data()
ref = d.ref
b = (
    d.t["bond"]
    .filter(pl.col("maturity") > ref)
    .join(d.t["company"].select("org_nr", "name", "tier"), on="org_nr", how="left")
    .with_columns(((pl.col("maturity") - pl.lit(ref)).dt.total_days() / 30.44).alias("months"))
    .sort("maturity")
)

total = b["nominal_sek"].sum() or 0
floating = b.filter(pl.col("coupon_type") == "floating")
secured_n = b.filter(pl.col("secured")).height
due12 = b.filter(pl.col("months") <= 12)
ui.hero(
    "Bonds · Outstanding",
    f"{b.height} property bonds, {ui.fmt_sek_m(total)} nominal.",
    f"{floating.height} pay a floating coupon over STIBOR 3M.",
    [
        ui.Stat(
            f"{(floating['margin_bp'].median() or 0):.0f}", "bp", "Median margin over STIBOR 3M"
        ),
        ui.Stat(
            f"{(due12['nominal_sek'].sum() or 0) / 1000:,.1f}",
            "bn SEK",
            f"Maturing within twelve months, <b>{due12.height} bonds</b>",
        ),
    ],
)

ui.section(1, "Margin against maturity")
c1, c2, c3, c4 = st.columns([3, 3, 2, 3], gap="large")
max_m = int(max(b["months"].max() or 12, 12)) + 1
months = c1.slider("Months to maturity", 0, max_m, (0, max_m))
mmax = int(b["margin_bp"].max() or 1000) + 50
margin = c2.slider("Margin over STIBOR, bp", 0, mmax, (0, mmax), step=25)
security = c3.selectbox("Security", ["All", "Secured", "Unsecured"])
tiers = c4.multiselect(
    "Issuer tier", ["listed", "bond"], format_func=TIER.get, placeholder="All tiers"
)

f = b.filter(pl.col("months").is_between(*months))
f = f.filter(pl.col("margin_bp").is_null() | pl.col("margin_bp").is_between(*margin))
if security != "All":
    f = f.filter(pl.col("secured") == (security == "Secured"))
if tiers:
    f = f.filter(pl.col("tier").is_in(tiers))

fl = f.filter(pl.col("coupon_type") == "floating")
fig = go.Figure()
for tier, colour, label in (("listed", INK, "Listed issuers"), ("bond", BLUE, "Bond-only issuers")):
    s = fl.filter(pl.col("tier") == tier)
    if s.is_empty():
        continue
    fig.add_scatter(
        x=s["maturity"].to_list(),
        y=s["margin_bp"].to_list(),
        mode="markers",
        name=label,
        marker=dict(
            size=[max(7, ((n or 0) / 1500) ** 0.5 * 22) for n in s["nominal_sek"]],
            color=colour,
            symbol=["square" if sec else "square-open" for sec in s["secured"]],
            line=dict(width=1, color=colour),
        ),
        text=s["name"].to_list(),
        customdata=s["nominal_sek"].to_list(),
        hovertemplate="%{text}<br>%{x|%Y-%m-%d} · STIBOR + %{y:.0f}bp · SEK %{customdata:,.0f}m<extra></extra>",
    )
fig.add_vline(x=ref + timedelta(days=365), line=dict(color=INK, width=1, dash="dot"))
fig.add_annotation(
    x=ref + timedelta(days=365),
    y=1,
    yref="paper",
    text="12 months",
    showarrow=False,
    xanchor="left",
    xshift=4,
    font=dict(size=11, color=INK),
)
fig.update_layout(
    height=420,
    hovermode="closest",
    yaxis=dict(ticksuffix="bp", rangemode="tozero"),
    legend=dict(font=dict(size=13.5)),
)
st.plotly_chart(fig, config=CONFIG, width="stretch")
ui.source_line(
    "Floating-rate bonds only. Marker area shows nominal; filled squares are secured, "
    f"open squares unsecured. {d.source_label('bond')}."
)

ui.section(2, "Outstanding bonds")
LIMIT = 25
show_all = f.height <= LIMIT or st.toggle(f"Show all {f.height} bonds", value=False)
shown = f if show_all else f.head(LIMIT)
rows = []
for r in shown.to_dicts():
    state = "high" if r["months"] <= 12 else "watch" if r["months"] <= 24 else "ok"
    coupon = (
        f"{r['benchmark']} + {r['margin_bp']:.0f}bp"
        if r["coupon_type"] == "floating"
        else f"{r['coupon_pct']:.2f}% fixed"
        if r["coupon_pct"]
        else "Fixed"
    )
    rows.append(
        {
            "isin": r["isin"],
            "issuer": f'<a href="issuer?org={r["org_nr"]}" target="_self">{ui.esc(r["name"])}</a>'
            f'<span class="sub">{ui.esc(r["isin"])}</span>',
            "nominal": ui.fmt_num(r["nominal_sek"])
            + (
                ""
                if r["currency"] == "SEK"
                else f'<span class="sub">{r["currency"]} {r["nominal"]:,.0f}m</span>'
            ),
            "mat": f"{ui.marker(state)}{ui.fmt_date(r['maturity'])}",
            "coupon": coupon,
            "sec": "Secured" if r["secured"] else "Unsecured",
            "src": ui.source_link(r["source_url"], "FIRDS"),
        }
    )
ui.table(
    rows,
    [
        ui.Col("issuer", "Issuer", "name", raw_html=True),
        ui.Col("nominal", "Nominal, SEK m", "num", raw_html=True),
        ui.Col("mat", "Maturity", "html"),
        ui.Col("coupon", "Coupon"),
        ui.Col("sec", "Security"),
        ui.Col("src", "Source", "html"),
    ],
    foot={"issuer": "Total", "nominal": ui.fmt_num(f["nominal_sek"].sum() or 0)},
)
ui.source_line(
    f"{'All ' if show_all else f'First {LIMIT} of '}{f.height} bonds, by maturity. "
    "Orange: maturity within 12 months; amber: 12–24 months. "
    f"{d.source_label('bond')}."
)

st.write("")
st.download_button(
    "Export CSV",
    f.drop("months").write_csv().encode("utf-8"),
    file_name=f"headroom_bonds_{ref}.csv",
    mime="text/csv",
)
