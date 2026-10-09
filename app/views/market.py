"""Market overview: maturity wall, events and rates."""

from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from ui import components as ui
from ui.data import get_data
from ui.labels import EVENT, SEVERITY_STATE
from ui.plotly_template import BLUE, CONFIG, INK, PAPER

d = get_data()
ref = d.ref
bands = d.cfg["motivated_seller"]["bands"]
stress_bp = d.cfg["motivated_seller"]["covenant"]["stress_bp"]
bonds = d.t["bond"].join(d.t["company"].select("org_nr", "name", "tier"), on="org_nr", how="left")
live = bonds.filter(pl.col("maturity") > ref)
due18 = live.filter(pl.col("maturity") <= ref + timedelta(days=548))
events = d.t["event"].join(d.t["company"].select("org_nr", "name"), on="org_nr", how="left")
wp = events.filter(
    (pl.col("type") == "written_procedure")
    & (pl.col("date") > ref - timedelta(days=365))
    & (pl.col("date") <= ref)
)

with_icr, breach = set(), set()
for org, card in d.cards.items():
    for r in card.covenants:
        if r.test == "icr" and r.kind == "maintenance" and r.headroom is not None:
            with_icr.add(org)
            if r.headroom_stressed is not None and r.headroom_stressed < 0:
                breach.add(org)

private = d.scores.filter(pl.col("tier") == "private")
severe_types = ["reconstruction", "bankruptcy_in_group", "liquidation", "going_concern"]
severe_orgs = (
    events.filter(
        pl.col("type").is_in(severe_types) & (pl.col("date") > ref - timedelta(days=365))
    )["org_nr"]
    .unique()
    .to_list()
)
flagged_private = private.filter(
    (pl.col("score") >= bands["high"]) | pl.col("org_nr").is_in(severe_orgs)
)

due_total = due18["nominal_sek"].sum() or 0
n_issuers = due18["org_nr"].n_unique()
n_bond_only = due18.filter(pl.col("tier") != "listed")["org_nr"].n_unique()
bn = f"{due_total / 1000:,.1f}"

# --------------------------------------------------------------------------- hero

stats = [
    ui.Stat(
        str(n_bond_only),
        f"of {n_issuers}",
        "Issuers with a maturity in that window that have <b>no listed equity</b>",
    )
]
if events.height:
    stats.append(
        ui.Stat(
            str(wp.height),
            "",
            f"Written-procedure notices in the last twelve months, at "
            f"<b>{wp['org_nr'].n_unique()} issuers</b>",
        )
    )
else:
    flt = live.filter(pl.col("coupon_type") == "floating")["nominal_sek"].sum() or 0
    total = live["nominal_sek"].sum() or 1
    stats.append(
        ui.Stat(
            f"{flt / total * 100:.0f}", "%", "Of outstanding nominal pays a <b>floating</b> coupon"
        )
    )
if private.height:
    stats.append(
        ui.Stat(
            str(flagged_private.height),
            f"of {private.height}",
            "Private companies flagged by score or insolvency-type events",
        )
    )
else:
    stats.append(
        ui.Stat(
            str(live["org_nr"].n_unique()),
            "",
            f"Swedish property companies with bonds outstanding, <b>{live.height} bonds</b>",
            ink=True,
        )
    )
ui.hero(
    f"Swedish property credit · {ui.fmt_date(ref, 'long')}",
    f"SEK {bn}bn of property bonds fall due in the next eighteen months.",
    "",
    stats,
)

# --------------------------------------------------------------------------- maturity wall


def quarter_start(x: date) -> date:
    return date(x.year, 3 * ((x.month - 1) // 3) + 1, 1)


with st.container(key="band_tint_wall"):
    ui.section(1, "Refinancing need")
    bounds, (y, m) = (
        [],
        (quarter_start(ref + timedelta(days=1)).year, quarter_start(ref + timedelta(days=1)).month),
    )
    for _ in range(13):
        bounds.append(date(y, m, 1))
        m += 3
        if m > 12:
            y, m = y + 1, m - 12
    quarters, horizon_end = bounds[:-1], bounds[-1]
    wall = live.with_columns(
        pl.col("maturity").map_elements(quarter_start, return_dtype=pl.Date).alias("q"),
        pl.when(pl.col("tier") == "listed")
        .then(pl.lit("Listed issuers"))
        .otherwise(pl.lit("Bond-only issuers"))
        .alias("grp"),
    )
    # drop empty quarters at the end of the horizon
    last_q = wall.filter(pl.col("maturity") < horizon_end)["q"].max()
    if last_q is not None:
        quarters = [q for q in quarters if q <= last_q]
    labels = [f"Q{(q.month - 1) // 3 + 1} {q.year}" for q in quarters]
    fig = go.Figure()
    totals = [0.0] * len(quarters)
    for grp, colour in (("Listed issuers", INK), ("Bond-only issuers", BLUE)):
        vals = []
        for i, q in enumerate(quarters):
            v = wall.filter((pl.col("q") == q) & (pl.col("grp") == grp))["nominal_sek"].sum() or 0.0
            vals.append(v)
            totals[i] += v
        fig.add_bar(
            x=labels,
            y=vals,
            name=grp,
            marker=dict(color=colour, line=dict(color=PAPER, width=1)),
            hovertemplate="%{x}<br>" + grp + ": SEK %{y:,.0f}m<extra></extra>",
        )
    fig.add_scatter(
        x=labels,
        y=totals,
        mode="text",
        text=[f"{t:,.0f}" if t else "" for t in totals],
        textposition="top center",
        textfont=dict(size=13, color=INK),
        showlegend=False,
        hoverinfo="skip",
    )
    fig.update_layout(
        height=400,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(showticklabels=False, range=[0, max(totals) * 1.15 or 1]),
        xaxis=dict(tickfont=dict(size=12.5)),
        legend=dict(font=dict(size=13.5)),
        bargap=0.45,
    )
    st.plotly_chart(fig, config=CONFIG, width="stretch")
    later = live.filter(pl.col("maturity") >= horizon_end)
    excl = (
        f" Excludes {later.height} bonds maturing after {labels[-1]} "
        f"({ui.fmt_sek_m(later['nominal_sek'].sum() or 0)})."
        if later.height
        else ""
    )
    fx_note = (
        " Bonds in other currencies converted at Riksbank mid rates."
        if (live["currency"] != "SEK").any()
        else ""
    )
    ui.source_line(
        f"SEK m, nominal outstanding, totals labelled.{fx_note}{excl} {d.source_label('bond')}."
    )

# --------------------------------------------------------------------------- events

ui.section(2, "Latest credit events")
recent = (
    events.filter(
        (pl.col("type") != "interim_report") & (pl.col("severity") >= 2) & (pl.col("date") <= ref)
    )
    .sort("date", descending=True)
    .head(3)
)
if recent.height:
    ui.news(
        [
            {
                "date": e["date"],
                "kind": EVENT.get(e["type"], e["type"]),
                "state": SEVERITY_STATE.get(e["severity"], "ok"),
                "title": e["title"],
                "company_html": f'<a href="issuer?org={e["org_nr"]}" target="_self">{ui.esc(e["name"])}</a>',
                "source_html": ui.source_link(e["source_url"], e["source_name"] or "Source"),
            }
            for e in recent.to_dicts()
        ]
    )
    ui.render('<div style="height:1rem"></div>')
    ui.source_line(f"{d.source_label('event')}. All events are on each issuer's page.")
else:
    ui.note("Press releases and trustee notices are connected in milestone 5.")

# --------------------------------------------------------------------------- rates

rates = d.t["rate"].sort("date")
names = {"policy_rate": "Policy rate", "tbill_3m": "Treasury bill, 3 months"}
with st.container(key="band_tint_rates"):
    ui.section(3, "Rates")
    cols = st.columns(3, gap="large")
    for col, (s_key, label) in zip(cols, names.items(), strict=False):
        last = rates.filter(pl.col("series") == s_key).tail(1).to_dicts()
        if not last:
            continue
        r = last[0]
        year_ago = (
            rates.filter(
                (pl.col("series") == s_key) & (pl.col("date") <= r["date"] - timedelta(days=365))
            )
            .tail(1)["value"]
            .to_list()
        )
        chg = (
            f", {r['value'] - year_ago[0]:+.2f} pp on a year".replace("-", "−") if year_ago else ""
        )
        with col:
            ui.render(
                ui.stats_html(
                    [
                        ui.Stat(
                            f"{r['value']:.2f}",
                            "%",
                            f"{label}, {ui.fmt_date(r['date'], 'long')}{chg}",
                            ink=(s_key == "policy_rate"),
                        )
                    ]
                )
            )
    with cols[2]:
        if with_icr:
            third = ui.Stat(
                str(len(breach)),
                f"of {len(with_icr)}",
                f"ICR covenant breaches if rates rise <b>{stress_bp}bp</b>",
            )
        else:
            sw = rates.filter(pl.col("series") == "swestr").tail(1).to_dicts()
            third = (
                ui.Stat(
                    f"{sw[0]['value']:.2f}",
                    "%",
                    f"SWESTR, overnight, {ui.fmt_date(sw[0]['date'], 'long')}",
                )
                if sw
                else None
            )
        if third:
            ui.render(ui.stats_html([third]))
    if d.fictional:
        ui.source_line(
            "Illustrative rate series, fictional. Live mode reads the Riksbank SWEA API."
        )
    else:
        ui.source_line(
            "Sveriges Riksbank, SWEA and SWESTR APIs. STIBOR is not published there since 2020, "
            f"so the 3-month treasury bill stands in for the floating base. {d.source_label('rate')}."
        )
