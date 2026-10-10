"""Underwriting: the market evidence an acquisition case needs, in one place.

Pick a municipality and a segment (and, optionally, a company from Companies). The page
gathers what the price, the rent, the costs and the debt should be, each with its source:

- Pricing: prime yields from broker research (Cushman & Wakefield MarketBeat), the
  valuation yields listed owners mark comparable stock at, SCB's sale prices against
  assessed values, and recent transactions.
- Rents, vacancy and costs: broker rents and vacancy, SCB rents, and listed owners'
  valuation assumptions and operating margins.
- Financing: live Riksbank rates, SCB's bank lending rates to companies, the margins on
  property bonds issued in the last eighteen months (ESMA FIRDS), and published notes on
  bank and alternative lending.
- Local demand: the municipality's figures from Locations.
- A hold-period cash flow, prefilled from the above, with a price at a target IRR, a
  sensitivity table and an Excel export with live formulas.

Hand-entered evidence lives in config/underwriting/market.yaml with source, period and page.
It works without a list of the target's properties: the inputs are market evidence for the
place and segment, and the company's own property value and EBITDA when one is chosen.
"""

from __future__ import annotations

import json
import statistics
from datetime import timedelta

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from headroom.model import underwriting as uw
from ui import components as ui
from ui.data import get_data
from ui.locdata import get_locations
from ui.plotly_template import BLUE, CONFIG, HIGH, MUTED
from ui.source_tips import source_tips

d = get_data()
D = get_locations()
M = uw.market_data()

# --------------------------------------------------------------------------- helpers


def src_text(row: dict) -> str:
    s = row.get("_source") or uw.source(row.get("source", ""))
    page = f", p. {row['page']}" if row.get("page") else ""
    note = f" {row['note']}." if row.get("note") else ""
    return f"{s.get('publisher', '')}, {s.get('title', '')}{page}.{note}".strip()


def src_link(row: dict) -> str:
    s = row.get("_source") or uw.source(row.get("source", ""))
    return ui.source_link(s.get("url"), s.get("publisher"))


def sv(html: str, tip: str) -> str:
    """A figure that shows its source on hover."""
    return f'<span data-src="{ui.esc(tip)}">{html}</span>' if tip and html != "–" else html


def pct_txt(v: float | None, digits: int = 2) -> str:
    """Per-cent values as stored in market.yaml (already in per cent)."""
    return "–" if v is None else f"{v:.{digits}f}%"


def bp_txt(v: float | None) -> str:
    return "–" if v is None else f"{v:+.0f} bp".replace("-", "−")


def sek(v: float | None, digits: int = 0) -> str:
    return "–" if v is None else f"{v:,.{digits}f}".replace("-", "−")


# --------------------------------------------------------------------------- choices

kommuner = (
    D.loc.filter(pl.col("level") == "kommun").sort("name")
    if D.available
    else pl.DataFrame({"code": ["0180"], "name": ["Stockholm"], "storstad": ["0010"]})
)
k_codes = kommuner["code"].to_list()
k_name = dict(zip(k_codes, kommuner["name"].to_list(), strict=True))
k_storstad = dict(zip(k_codes, kommuner["storstad"].to_list(), strict=True))
k_by_name = {v.lower(): k for k, v in k_name.items()}

sc = d.scores.sort("name")
orgs = ["", *sc["org_nr"].to_list()]
org_name = dict(zip(sc["org_nr"].to_list(), sc["name"].to_list(), strict=True))

state = st.session_state
qp = st.query_params
# A link from a company or place page (?org=, ?kommun=, ?segment=) resets the choices once;
# after that the selectboxes rule until the link changes.
q_key = (qp.get("org"), qp.get("kommun"), qp.get("segment"))
if any(q_key) and state.get("uw_q") != q_key:
    for k in ("uw_org", "uw_kommun", "uw_segment"):
        state.pop(k, None)
    state["uw_q"] = q_key
if "uw_org" not in state:
    state["uw_org"] = qp.get("org") if qp.get("org") in org_name else ""
if "uw_kommun" not in state:
    k = qp.get("kommun")
    if k not in k_name and state["uw_org"]:
        co = d.company(state["uw_org"])
        k = k_by_name.get((co.get("city") or "").lower())
    state["uw_kommun"] = k if k in k_name else "0180"
if "uw_segment" not in state:
    seg = qp.get("segment")
    if seg not in uw.SEGMENTS and state["uw_org"]:
        mix = json.loads(d.company(state["uw_org"]).get("segment_mix") or "{}")
        seg = max(mix, key=mix.get) if mix else None
    state["uw_segment"] = seg if seg in uw.SEGMENTS else "residential"

kommun = state["uw_kommun"]
segment = state["uw_segment"]
org = state["uw_org"] or None
market = uw.market_of(kommun, segment, k_storstad.get(kommun))
mk_label = uw.MARKETS[market]
seg_label = uw.SEGMENTS[segment]
place = k_name[kommun]
feat = D.feat(kommun) if D.available else {}
lan = kommun[:2]
lan_feat = D.feat(lan) if D.available else {}
nat = D.feat("00") if D.available else {}

# --------------------------------------------------------------------------- financing data

rates = d.t["rate"]


def latest(series: str) -> tuple[float | None, str | None, float | None]:
    """Latest value, its date, and the change over roughly three months (in bp)."""
    s = rates.filter(pl.col("series") == series).sort("date")
    if not s.height:
        return None, None, None
    v, dt = s["value"][-1], s["date"][-1]
    then = s.filter(pl.col("date") <= dt - timedelta(days=90))
    chg = (v - then["value"][-1]) * 100 if then.height else None
    return v, str(dt), chg


bonds = d.t["bond"].join(d.t["company"].select("org_nr", "name", "tier"), on="org_nr", how="left")
recent = (
    bonds.filter(
        (pl.col("coupon_type") == "floating")
        & (pl.col("currency") == "SEK")
        & pl.col("margin_bp").is_not_null()
        & (pl.col("issue_date") >= d.ref - timedelta(days=548))
    )
    .with_columns(
        ((pl.col("maturity") - pl.col("issue_date")).dt.total_days() / 365.25).alias("tenor")
    )
    .filter(pl.col("tenor") <= 15)  # perpetual hybrids are not senior funding
)
bond_only = recent.filter(pl.col("tier") != "listed")
med_all = recent["margin_bp"].median() if recent.height else None
med_bond_only = bond_only["margin_bp"].median() if bond_only.height else None

tbill, tbill_dt, _ = latest("tbill_3m")
bank_float, bank_dt, _ = latest("bank_nfc_floating")
mb5, mb5_dt, _ = latest("mb_5y")
debt_cost = bank_float  # SCB: new floating-rate bank loans to non-financial companies
debt_tip = (
    f"SCB TAB5780: average rate on new floating-rate bank loans to non-financial companies, "
    f"{bank_dt}. Not property-specific; secured property loans at moderate LTV often price "
    "below it, high-yield property bonds above it."
)
if debt_cost is None and tbill is not None and med_all is not None:
    debt_cost = tbill + med_all / 100
    debt_tip = "3-month treasury bill plus the median margin of recent property bonds."

# --------------------------------------------------------------------------- yields

prime = uw.pick("prime_yields", segment, market)
peers = uw.rows("valuation_yields", segment)
peer_vals = [p["value"] for p in peers]
local_peers = [p for p in peers if not p.get("markets") or market in p["markets"]] or peers
ref_yield = (
    prime["value"]
    if prime
    else (statistics.median(p["value"] for p in local_peers) if local_peers else None)
)
ref_tip = (
    src_text(prime)
    if prime
    else (
        "No broker publishes a prime yield for this segment in this market; median of the "
        "listed owners' valuation yields that cover it: "
        + "; ".join(f"{p['company']} {p['value']}%" for p in local_peers)
    )
    if local_peers
    else ""
)
other_primes = uw.rows("prime_yields", segment)

# --------------------------------------------------------------------------- hero

spread = (ref_yield - debt_cost) * 100 if ref_yield is not None and debt_cost else None
if spread is None:
    lead = "Debt and pricing evidence for this segment is thin; see the sources below."
elif spread >= 50:
    lead = (
        f"The yield clears the cost of debt by {spread:.0f} bp: leverage adds to the cash "
        "yield from day one."
    )
elif spread >= 0:
    lead = (
        f"The yield is only {spread:.0f} bp above the cost of debt: leverage adds little; "
        "returns rest on rent growth and the exit."
    )
else:
    lead = (
        f"Debt costs {-spread:.0f} bp more than the yield: leverage dilutes the cash yield at "
        "entry, so the case rests on rent growth, value-add and the exit yield."
    )

if not prime and other_primes:
    lo_p = min(r["value"] for r in other_primes)
    hi_p = max(r["value"] for r in other_primes)
    lead += (
        f" Brokers publish prime {seg_label.lower()} yields only for the metro markets "
        f"({lo_p:.2f}–{hi_p:.2f}%); stock elsewhere is marked higher."
    )
yield_word = "prime yield" if prime else "listed owners' valuation yield"
headline = (
    f"{seg_label} in {place} prices at a {yield_word} of {pct_txt(ref_yield)}; "
    f"new bank debt costs about {pct_txt(debt_cost)}."
    if ref_yield is not None and debt_cost is not None
    else f"{seg_label} in {place}."
)
c1, c2, c3 = st.columns([1.2, 1, 1.6], gap="large")
c1.selectbox("Municipality", k_codes, format_func=k_name.get, key="uw_kommun")
c2.selectbox("Segment", list(uw.SEGMENTS), format_func=uw.SEGMENTS.get, key="uw_segment")
c3.selectbox(
    "Company (optional)",
    orgs,
    format_func=lambda o: org_name.get(o, "None: underwrite an asset"),
    key="uw_org",
)

ui.hero(
    f"Underwriting · {place} · {seg_label}",
    headline,
    lead + f" Market as the brokers define it: <b>{mk_label}</b>. Every figure shows its source on "
    "hover; nothing on this page needs a list of the target's properties.",
    [
        ui.Stat(pct_txt(ref_yield), "", yield_word.capitalize(), tip=ref_tip),
        ui.Stat(
            (
                f"{min(peer_vals):.1f}–{max(peer_vals):.1f}%"
                if len(set(peer_vals)) > 1
                else pct_txt(peer_vals[0], 1)
            )
            if peer_vals
            else "–",
            "",
            "Listed owners' valuation yields, same segment",
            tip="; ".join(f"{p['company']} {p['value']}% ({src_text(p)})" for p in peers),
        ),
        ui.Stat(pct_txt(debt_cost), "", "Indicative cost of new bank debt", tip=debt_tip),
        ui.Stat(
            bp_txt(spread),
            "",
            "Yield minus cost of debt",
            state="high" if spread is not None and spread < 0 else None,
        ),
    ],
)

# --------------------------------------------------------------------------- 1. pricing

ui.section(1, "Pricing", f"What {seg_label.lower()} sells for, and where owners mark it.")

yrows = []
for r in uw.rows("prime_yields", segment):
    yrows.append(
        {
            "_class": "me" if r["market"] == market else None,
            "kind": "Prime yield",
            "who": uw.MARKETS.get(r["market"], r["market"]),
            "y": sv(pct_txt(r["value"]), src_text(r)),
            "note": r.get("note", ""),
            "src": src_link(r),
        }
    )
for r in peers:
    yrows.append(
        {
            "kind": "Valuation yield, listed owner",
            "who": r["company"],
            "y": sv(pct_txt(r["value"]), src_text(r)),
            "note": r.get("focus", ""),
            "src": src_link(r),
        }
    )
if segment == "retail":
    for kind in ("shopping_centre", "retail_park"):
        for r in uw.rows("prime_yields", kind):
            yrows.append(
                {
                    "_class": "me" if r["market"] == market else None,
                    "kind": f"Prime yield, {r['note'].lower()}",
                    "who": uw.MARKETS.get(r["market"], r["market"]),
                    "y": sv(pct_txt(r["value"]), src_text(r)),
                    "note": "",
                    "src": src_link(r),
                }
            )
if yrows:
    ui.table(
        yrows,
        [
            ui.Col("kind", "Measure"),
            ui.Col("who", "Market or owner", "name"),
            ui.Col("y", "Yield", "num", raw_html=True),
            ui.Col("note", "Note"),
            ui.Col("src", "Source", "html"),
        ],
    )
    ui.source_line(
        "Prime yields: the lowest yield a best-in-class asset in the market would trade at "
        "(Cushman & Wakefield, Q2 2026). Valuation yields: the weighted average "
        "direktavkastningskrav in the owner's valuation at 30 June 2026, for its whole "
        "portfolio. The highlighted row is the market of the chosen municipality."
    )
else:
    ui.note("No published yield for this segment yet. Add one to config/underwriting/market.yaml.")

# SCB: price paid against assessed value in the county
KT = {
    "residential": ("rental_block_kt", "Rental blocks (hyreshus)", "TAB1156"),
    "logistics": ("warehouse_kt", "Warehouses (lager)", "TAB1158"),
    "light_industrial": ("industrial_kt", "Industrial units", "TAB1158"),
}
if segment in KT and D.available:
    key, label, table = KT[segment]
    lan_name = D.name(lan)
    county_v, nat_v = lan_feat.get(key), nat.get(key)
    per = lan_feat.get(f"{key}_period")
    tip = (
        f"SCB {table}: mean purchase price over mean assessed value for {label.lower()} sold "
        f"in {per}, all sales with a registered title. A K/T of 1.5 means buyers paid 1.5 "
        "times the tax value."
    )
    ui.facts(
        [
            (f"Price / assessed value, {lan_name}", sv(ui.fmt_x(county_v), tip)),
            ("Price / assessed value, Sweden", sv(ui.fmt_x(nat_v), tip)),
            ("Year", per or "–"),
            ("Type", label),
        ]
    )
    ser = D.series(key, [lan, "00"]).filter(pl.col("period") >= "2010")
    if ser.height:
        fig = go.Figure()
        for code, colour, name in ((lan, BLUE, lan_name), ("00", MUTED, "Sweden")):
            s = ser.filter(pl.col("code") == code)
            fig.add_scatter(
                x=s["period"].to_list(),
                y=s["value"].to_list(),
                name=name,
                mode="lines+markers",
                line=dict(color=colour),
                hovertemplate="%{x}: %{y:.2f}x<extra>" + name + "</extra>",
            )
        fig.update_layout(height=230, yaxis_title="K/T", hovermode="closest")
        st.plotly_chart(fig, config=CONFIG, width="stretch")
    ui.source_line(
        f"Source: SCB {ui.source_link(D.source(key, 'lan').get('source_url'), table)}. "
        "County level: SCB does not publish sale prices per municipality for these types."
    )

deals = [r for r in uw.rows("deals") if r["segment"] == segment] or uw.rows("deals")
ui.render('<h3 class="hr-sub">Transactions with a disclosed price</h3>')
ui.table(
    [
        {
            "period": r["period"],
            "place": r["place"],
            "asset": r["asset"],
            "parties": f"{r['seller']} → {r['buyer']}",
            "sqm": sek(r.get("sqm")),
            "price": sv(sek(r.get("price_sek_m")), src_text(r)),
            "psqm": sek(r.get("sek_per_sqm")),
            "src": src_link(r),
        }
        for r in deals
    ],
    [
        ui.Col("period", "Period", "dim"),
        ui.Col("place", "Place", "name"),
        ui.Col("asset", "Asset"),
        ui.Col("parties", "Seller → buyer"),
        ui.Col("sqm", "Sq m", "num"),
        ui.Col("price", "Price, SEK m", "num", raw_html=True),
        ui.Col("psqm", "SEK per sq m", "num"),
        ui.Col("src", "Source", "html"),
    ],
)
if not [r for r in uw.rows("deals") if r["segment"] == segment]:
    ui.source_line(f"No {seg_label.lower()} deal with a disclosed price in the sources; all shown.")

# --------------------------------------------------------------------------- 2. rents and costs

ui.section(2, "Rents, vacancy and costs", "What the income line should be.")
rrows = []
for r in uw.rows("rents", segment):
    rrows.append(
        {
            "_class": "me" if r["market"] == market else None,
            "kind": "Rent",
            "who": uw.MARKETS.get(r["market"], r["market"]),
            "v": sv(f"SEK {sek(r['value'])}/sq m", src_text(r)),
            "note": r.get("note", ""),
            "src": src_link(r),
        }
    )
if segment == "residential" and D.available:
    rent_k = feat.get("rent_sqm")
    if rent_k:
        rrows.append(
            {
                "_class": "me",
                "kind": "Median rent, all rental flats",
                "who": place,
                "v": sv(
                    f"SEK {sek(rent_k)}/sq m",
                    f"SCB TAB4603, {feat.get('rent_sqm_period')}: median annual rent per sq m, "
                    "rental flats in the municipality (published for the 20 largest only).",
                ),
                "note": "Existing stock, regulated (bruksvärde)",
                "src": ui.source_link(D.source("rent_sqm").get("source_url"), "SCB"),
            }
        )
    region = k_storstad.get(kommun)
    if region not in ("0010", "0020", "0030"):
        region = "0040" if (feat.get("population") or 0) > 75_000 else "0041"
    nb = D.series("newbuild_rent_presumption", [region])
    if nb.height:
        rname = {
            "0010": "Greater Stockholm",
            "0020": "Greater Gothenburg",
            "0030": "Greater Malmö",
            "0040": "Municipalities over 75,000 (outside metro areas)",
            "0041": "Municipalities under 75,000 (outside metro areas)",
        }[region]
        rrows.append(
            {
                "kind": "New-build presumption rent",
                "who": rname,
                "v": sv(
                    f"SEK {sek(nb['value'][-1])}/sq m",
                    f"SCB TAB6417, {nb['period'][-1]}: average annual rent per sq m in new "
                    "rental flats let at presumption rent (presumtionshyra), completed that year.",
                ),
                "note": "The rent a new project can set for its first 15 years",
                "src": ui.source_link(
                    D.source("newbuild_rent_presumption", "storstad").get("source_url"), "SCB"
                ),
            }
        )
for r in uw.rows("vacancy", segment):
    rrows.append(
        {
            "_class": "me" if r["market"] == market else None,
            "kind": "Vacancy",
            "who": uw.MARKETS.get(r["market"], r["market"]),
            "v": sv(pct_txt(r["value"], 1), src_text(r)),
            "note": r.get("note", ""),
            "src": src_link(r),
        }
    )
for r in uw.rows("noi_margins", segment):
    rrows.append(
        {
            "kind": "Operating margin (NOI / rent)",
            "who": r["company"],
            "v": sv(pct_txt(r["value"], 1), src_text(r)),
            "note": r.get("note", ""),
            "src": src_link(r),
        }
    )
if rrows:
    ui.table(
        rrows,
        [
            ui.Col("kind", "Measure"),
            ui.Col("who", "Market or owner", "name"),
            ui.Col("v", "Value, per year", "num", raw_html=True),
            ui.Col("note", "Note"),
            ui.Col("src", "Source", "html"),
        ],
    )
else:
    ui.note(
        f"No published rent or vacancy for {seg_label.lower()} in the sources. For community "
        "service and hotels, rent is usually a long lease to a tenant or operator; use the "
        "lease in the calculator below."
    )

# --------------------------------------------------------------------------- 3. financing

ui.section(3, "Financing", "What the debt costs and how much of it there is.")
RATE_ROWS = [
    ("policy_rate", "Riksbank policy rate", "Riksbank"),
    ("swestr", "SWESTR, overnight", "Riksbank"),
    ("tbill_3m", "3-month T-bill (STIBOR 3M proxy)", "Riksbank"),
    ("mb_2y", "2-year mortgage bond", "Riksbank"),
    ("mb_5y", "5-year mortgage bond (5-year swap proxy)", "Riksbank"),
    ("gvb_5y", "5-year government bond", "Riksbank"),
    ("gvb_10y", "10-year government bond", "Riksbank"),
    ("bank_nfc_floating", "New bank loans to companies, floating", "SCB"),
    ("bank_nfc_5y_plus", "New bank loans to companies, fixed 5y+", "SCB"),
]
rate_rows = []
for key, label, src in RATE_ROWS:
    v, dt, chg = latest(key)
    if v is None:
        continue
    url = rates.filter(pl.col("series") == key)["source_url"][-1]
    rate_rows.append(
        {
            "label": label,
            "v": sv(pct_txt(v), f"{src}, {'TAB5780' if src == 'SCB' else 'SWEA/SWESTR'}, {dt}."),
            "chg": bp_txt(chg),
            "src": ui.source_link(url, src),
        }
    )
left, right = st.columns([1, 1.1], gap="large")
with left:
    ui.table(
        rate_rows,
        [
            ui.Col("label", "Rate", "name"),
            ui.Col("v", "Level", "num", raw_html=True),
            ui.Col("chg", "3 months", "num"),
            ui.Col("src", "Source", "html"),
        ],
    )
with right:
    fig = go.Figure()
    for key, name, colour in (
        ("tbill_3m", "3m T-bill", MUTED),
        ("mb_5y", "5y mortgage bond", BLUE),
        ("bank_nfc_floating", "Bank loans to companies, floating", HIGH),
    ):
        s = rates.filter(
            (pl.col("series") == key) & (pl.col("date") >= d.ref - timedelta(days=3 * 365))
        ).sort("date")
        if s.height:
            fig.add_scatter(
                x=s["date"].to_list(),
                y=s["value"].to_list(),
                name=name,
                mode="lines",
                line=dict(color=colour, shape="hv" if key.startswith("bank") else "linear"),
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra>" + name + "</extra>",
            )
    fig.update_layout(height=320, yaxis_ticksuffix="%", hovermode="closest")
    st.plotly_chart(fig, config=CONFIG, width="stretch")

if recent.height:
    ui.render(
        '<h3 class="hr-sub">Margins on property bonds issued in the last eighteen months</h3>'
    )
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        fig = go.Figure()
        for tier, colour, name in (
            ("listed", BLUE, "Listed issuer"),
            (None, HIGH, "Issuer without listed shares"),
        ):
            s = recent.filter(pl.col("tier") == "listed") if tier else bond_only
            fig.add_scatter(
                x=s["tenor"].to_list(),
                y=s["margin_bp"].to_list(),
                mode="markers",
                name=name,
                marker=dict(color=colour, size=7, opacity=0.8),
                customdata=list(
                    zip(
                        s["name"].to_list(),
                        s["issue_date"].cast(pl.Utf8).to_list(),
                        s["nominal_sek"].to_list(),
                        strict=True,
                    )
                ),
                hovertemplate="%{customdata[0]}<br>Issued %{customdata[1]}, SEK %{customdata[2]:,.0f}m"
                "<br>%{x:.1f} years, STIBOR + %{y:.0f} bp<extra></extra>",
            )
        fig.update_layout(
            height=320, xaxis_title="Tenor, years", yaxis_title="Margin, bp", hovermode="closest"
        )
        st.plotly_chart(fig, config=CONFIG, width="stretch")
    with right:
        buckets = recent.with_columns(
            pl.when(pl.col("tenor") <= 2.5)
            .then(pl.lit("Up to 2.5 years"))
            .when(pl.col("tenor") <= 4)
            .then(pl.lit("2.5–4 years"))
            .otherwise(pl.lit("Over 4 years"))
            .alias("bucket")
        )
        order = {"Up to 2.5 years": 0, "2.5–4 years": 1, "Over 4 years": 2}
        agg = buckets.group_by("bucket").agg(
            pl.len().alias("n"),
            pl.col("margin_bp").quantile(0.25).alias("q1"),
            pl.col("margin_bp").median().alias("med"),
            pl.col("margin_bp").quantile(0.75).alias("q3"),
            pl.col("margin_bp").max().alias("mx"),
        )
        ui.table(
            [
                {
                    "bucket": r["bucket"],
                    "n": r["n"],
                    "q1": f"{r['q1']:.0f}",
                    "med": f"{r['med']:.0f}",
                    "q3": f"{r['q3']:.0f}",
                    "mx": f"{r['mx']:.0f}",
                }
                for r in sorted(agg.to_dicts(), key=lambda r: order[r["bucket"]])
            ],
            [
                ui.Col("bucket", "Tenor", "name"),
                ui.Col("n", "Bonds", "num"),
                ui.Col("q1", "Lower quartile", "num"),
                ui.Col("med", "Median", "num"),
                ui.Col("q3", "Upper quartile", "num"),
                ui.Col("mx", "Highest", "num"),
            ],
        )
        q3_all = recent["margin_bp"].quantile(0.75)
        hy = (
            recent.filter(pl.col("margin_bp") >= 300)
            .group_by("name")
            .agg(pl.col("margin_bp").max())
            .sort("margin_bp", descending=True)
        )
        lo = (
            recent.filter(pl.col("margin_bp") <= recent["margin_bp"].quantile(0.25))
            .group_by("name")
            .agg(pl.len())
            .sort("len", descending=True)
            .head(4)["name"]
            .to_list()
        )
        ui.source_line(
            "Margin over STIBOR 3M, bp. The tightest-priced issuers include "
            f"{', '.join(lo)}. From {q3_all:.0f} bp up is the high-yield end, closest "
            "to what a levered private owner pays for unsecured debt"
            + (f" ({', '.join(hy['name'].head(5).to_list())})." if hy.height else ".")
        )
    ui.source_line(
        f"{recent.height} floating-rate SEK bonds of the property issuers in Headroom, issued "
        f"since {d.ref - timedelta(days=548)}, perpetual hybrids excluded. Margins as "
        f"registered in ESMA FIRDS ({d.as_of('bond')}). Add the 3-month rate above for the "
        "all-in coupon."
    )

# leverage of listed owners
fin = (
    d.t["financials"]
    .sort("period_end")
    .group_by("org_nr")
    .agg(pl.all().last())
    .join(d.t["company"].select("org_nr", "tier"), on="org_nr")
    .filter(pl.col("tier") != "private")
)


def med(col: str, lo: float, hi: float) -> float | None:
    s = fin.filter(pl.col(col).is_between(lo, hi))[col]
    return s.median() if s.len() else None


ui.facts(
    [
        ("Median LTV, listed and bond issuers", ui.fmt_pct(med("ltv", 0, 1))),
        ("Median interest cover", ui.fmt_x(med("icr", 0, 20), 1)),
        ("Median average interest rate", ui.fmt_pct(med("avg_rate", 0, 0.15), 2)),
        ("Companies", f"{fin.height}"),
    ]
)
fin_notes = M.get("financing") or []
if fin_notes:
    ui.table(
        [
            {
                "topic": r["topic"],
                "text": sv(ui.esc(r["text"]), src_text({**r, "_source": uw.source(r["source"])})),
                "src": src_link({**r, "_source": uw.source(r["source"])}),
            }
            for r in fin_notes
        ],
        [
            ui.Col("topic", "Topic", "name"),
            ui.Col("text", "Published evidence", "html"),
            ui.Col("src", "Source", "html"),
        ],
    )

# --------------------------------------------------------------------------- 4. local demand

if D.available:
    ui.section(4, f"Demand in {place}", "From Locations; the full picture is one click away.")
    bme = feat.get("bme")
    bme_txt = (
        "–" if bme is None else "Shortage" if bme < 0.5 else "Balance" if bme < 1.5 else "Surplus"
    )
    if segment in ("logistics", "light_industrial"):
        items = [
            ("People within 100 km", ui.fmt_people(feat.get("catchment_100km"))),
            ("Transport and warehousing jobs, share", ui.fmt_pct(feat.get("logistics_share"), 1)),
            ("Their growth per year", ui.fmt_delta(feat.get("logistics_growth"))),
            ("Nearest intermodal terminal", f"{ui.fmt_km(feat.get('dist_terminal'))}"),
        ]
    else:
        items = [
            ("Growth per year, 5 years", ui.fmt_delta(feat.get("growth_5y"))),
            ("New residents per completed home", ui.fmt_num(feat.get("housing_pressure"), 1)),
            ("Housing market (municipality's view)", bme_txt),
            ("Homes started per 1,000, 4 quarters", ui.fmt_num(feat.get("pipeline"), 1)),
        ]
    ui.facts(items)
    ui.source_line(
        f'<a href="locations?level=kommun&code={kommun}" target="_self">Open {ui.esc(place)} '
        "on Locations</a> for sources, peers and the areas inside it."
    )

# --------------------------------------------------------------------------- 5. market

ui.section(5, "Transaction market", "How deep the market is right now.")
vq = (M.get("volumes") or {}).get("quarter") or {}
if vq:
    tot = sum(r["sek_m"] for r in vq["rows"])
    vs = uw.source(vq["source"])
    ui.table(
        [
            {
                "_class": "me" if r["segment"] == segment else None,
                "seg": r.get("label") or uw.SEGMENTS.get(r["segment"], r["segment"]),
                "n": r["deals"],
                "v": sek(r["sek_m"]),
                "share": ui.fmt_pct(r["sek_m"] / tot),
                "y": pct_txt(r.get("prime_yield")),
            }
            for r in vq["rows"]
        ],
        [
            ui.Col("seg", "Segment", "name"),
            ui.Col("n", "Deals", "num"),
            ui.Col("v", "Volume, SEK m", "num"),
            ui.Col("share", "Share", "num"),
            ui.Col("y", "Prime yield", "num"),
        ],
        foot={"seg": "Total", "v": sek(tot), "n": str(sum(r["deals"] for r in vq["rows"]))},
    )
    ui.source_line(
        f"Q{vq['period'][-1]} {vq['period'][:4]}. {vq['note']}. Source: "
        f"{ui.source_link(vs.get('url'), vs.get('publisher') + ', ' + vs.get('title'))}."
    )
totals = (M.get("volumes") or {}).get("totals") or []
if totals:
    ui.table(
        [
            {
                "period": r["period"],
                "v": r["value"],
                "note": r.get("note", ""),
                "src": ui.source_link(
                    uw.source(r["source"]).get("url"), uw.source(r["source"]).get("publisher")
                ),
            }
            for r in totals
        ],
        [
            ui.Col("period", "Period", "dim"),
            ui.Col("v", "Swedish volume", "num"),
            ui.Col("note", "Note"),
            ui.Col("src", "Source", "html"),
        ],
    )
    ui.source_line(
        "Sources count differently (deal-size thresholds, signed or closed, currency), so "
        "their totals differ; read each against its own history."
    )

# --------------------------------------------------------------------------- 6. calculator

ui.section(
    6,
    "Underwrite it",
    "A hold-period cash flow, prefilled from the evidence above. Change any input.",
)

co_fin = None
if org:
    f_org = d.t["financials"].filter(pl.col("org_nr") == org).sort("period_end")
    if f_org.height:
        co_fin = f_org.row(-1, named=True)
use_company = bool(
    co_fin and co_fin.get("property_value") and co_fin.get("ebitda") and co_fin["ebitda"] > 0
)

noi_margin_row = next(iter(uw.rows("noi_margins", segment)), None)
rent_row = uw.pick("rents", segment, market)
default_rent = rent_row["value"] if rent_row else 1500.0
rent_note = src_text(rent_row) if rent_row else "No published rent for this segment; assumption."
if segment == "residential" and feat.get("rent_sqm"):
    default_rent = float(feat["rent_sqm"])
    rent_note = (
        f"SCB TAB4603: median rent of rental flats in {place}, {feat.get('rent_sqm_period')}."
    )
default_margin = (noi_margin_row["value"] / 100) if noi_margin_row else 0.75
margin_note = src_text(noi_margin_row) if noi_margin_row else "Assumption."
vac_row = uw.pick("vacancy", segment, market) if segment == "light_industrial" else None
default_vac = 0.02 if segment == "residential" else 0.05
vac_note = (
    src_text(vac_row)
    if vac_row
    else "Assumption: long-run structural vacancy (2% for housing with a queue, 5% otherwise)."
)


def pct_input(label: str, value: float, step: float = 0.25, help: str | None = None) -> float:
    """A rate entered in per cent (4.80), returned as a fraction (0.048)."""
    return (
        st.number_input(
            f"{label}, %", value=round(value * 100, 2), step=step, format="%.2f", help=help
        )
        / 100
    )


if org and not use_company:
    ui.note(
        f"{ui.esc(org_name[org])}'s latest report has no usable property value and EBITDA, so "
        "the model below underwrites an asset from market evidence instead."
    )

with st.container(key="uw_inputs"):
    a1, a2, a3 = st.columns(3, gap="large")
    with a1:
        ui.render('<div class="hr-eyebrow">Income</div>')
        if use_company:
            st.caption(
                f"{org_name[org]}: property value and EBITDA from its "
                f"{co_fin['period_end']} report."
            )
            price_in = st.number_input(
                "Purchase price, SEK m", value=float(round(co_fin["property_value"], 1)), step=10.0
            )
            noi_in = st.number_input(
                "NOI year 1, SEK m (EBITDA as proxy)",
                value=float(round(co_fin["ebitda"], 1)),
                step=1.0,
            )
            area = None
        else:
            area = st.number_input(
                "Lettable area, sq m", value=10_000, step=500, min_value=1, format="%d"
            )
            rent = st.number_input(
                "Rent, SEK per sq m and year",
                value=round(default_rent),
                step=50,
                format="%d",
                help=rent_note,
            )
            vac = pct_input("Vacancy", default_vac, 0.5, vac_note)
            margin = pct_input("Operating margin (NOI / rent)", default_margin, 1.0, margin_note)
            noi_in = area * rent * (1 - vac) * margin / 1e6
        growth = pct_input(
            "NOI growth per year",
            0.02,
            help="Default 2%: the Riksbank's inflation target, and the long-term rent "
            "indexation NP3 and Sveafastigheter assume in their valuations from 2028.",
        )
    with a2:
        ui.render('<div class="hr-eyebrow">Price and exit</div>')
        if not use_company:
            entry_y = pct_input(
                "Entry yield", (ref_yield or 5.0) / 100, help=ref_tip or "Assumption."
            )
            price_in = noi_in / entry_y if entry_y else 0.0
        exit_y = pct_input(
            "Exit yield",
            (ref_yield or 5.0) / 100 + 0.0025,
            help="Default: the entry reference plus 25 bp for an older asset at exit.",
        )
        hold = int(st.number_input("Hold, years", value=5, min_value=1, max_value=15, step=1))
        entry_cost = pct_input(
            "Transaction costs on purchase",
            0.015,
            help="Advisers and due diligence. Stamp duty (4.25% for companies) is not paid when "
            "the property is bought in a company wrapper; the deferred-tax discount that "
            "replaces it is negotiated in the price.",
        )
        exit_cost = pct_input("Transaction costs on sale", 0.01)
    with a3:
        ui.render('<div class="hr-eyebrow">Debt and target</div>')
        ltv = pct_input(
            "Loan-to-value",
            0.55,
            5.0,
            help="Default 55%: under the 60% above which Nordic banks turn cautious (JLL, Nordic "
            "Outlook Spring 2026, p. 6).",
        )
        rate = pct_input("All-in interest rate", (debt_cost or 4.5) / 100, help=debt_tip)
        capex = st.number_input("Capex per year, SEK m", value=0.0, step=0.5)
        target = pct_input(
            "Target levered IRR",
            0.12,
            1.0,
            help="The return the equity must make; used to solve for the price.",
        )

a = uw.Assumptions(
    price=max(price_in, 0.01),
    noi=max(noi_in, 0.0),
    rent_growth=growth,
    ltv=ltv,
    interest=rate,
    exit_yield=max(exit_y, 0.0001),
    hold=hold,
    entry_cost=entry_cost,
    exit_cost=exit_cost,
    capex=capex,
)
res = uw.run(a)
bid = uw.solve_price(a, target)

out_stats = [
    ui.Stat(ui.fmt_pct(res.irr_levered, 1), "", "Levered IRR", ink=True),
    ui.Stat(ui.fmt_pct(res.irr_unlevered, 1), "", "Unlevered IRR"),
    ui.Stat(ui.fmt_x(res.moic, 2), "", "Equity multiple"),
    ui.Stat(
        ui.fmt_x(res.icr_y1, 2),
        "",
        "Interest cover, year 1",
        state="high" if res.icr_y1 is not None and res.icr_y1 < 1.5 else None,
    ),
]
ui.render(ui.stats_html(out_stats).replace('class="hr-stats"', 'class="hr-stats row"', 1))
ui.facts(
    [
        ("Price", ui.fmt_sek_m(a.price)),
        ("NOI, year 1", ui.fmt_sek_m(a.noi)),
        ("Entry yield", ui.fmt_pct(a.entry_yield, 2)),
        ("Price per sq m", f"SEK {sek(a.price * 1e6 / area)}" if area else "–"),
        ("Debt / equity", f"{ui.fmt_sek_m(res.debt)} / {ui.fmt_sek_m(res.equity)}"),
        ("Debt yield, year 1", ui.fmt_pct(res.debt_yield, 1)),
        ("Cash yield on equity, year 1", ui.fmt_pct(res.cash_yield_y1, 1)),
        ("Exit value", ui.fmt_sek_m(res.exit_value)),
    ]
)

if bid is not None:
    gap = bid / a.price - 1
    word = "below" if gap < 0 else "above"
    msg = (
        f"<b>Price at a {ui.fmt_pct(target, 0)} levered IRR: {ui.fmt_sek_m(bid)}</b>, "
        f"{ui.fmt_pct(abs(gap), 0)} {word} the price above "
        f"(an entry yield of {ui.fmt_pct(a.noi / bid, 2)})."
    )
    if use_company:
        msg += (
            f" Against {org_name[org]}'s reported property value, that is the discount the "
            "seller would have to accept: the gap a motivated seller closes."
        )
    ui.note(msg)
if use_company and ref_yield:
    at_market = a.noi / (ref_yield / 100)
    ui.note(
        f"At the market reference yield of {pct_txt(ref_yield)}, this NOI is worth "
        f"{ui.fmt_sek_m(at_market)}: {ui.fmt_pct(abs(at_market / a.price - 1), 0)} "
        f"{'below' if at_market < a.price else 'above'} the reported property value. EBITDA "
        "includes central administration, so it understates property NOI somewhat."
    )

# cash flow table
cf_cols = [ui.Col("k", "", "name")] + [ui.Col(f"y{t}", f"Year {t}", "num") for t in res.years]
cf_rows = []
for label, vals in (
    ("NOI", res.noi),
    ("Capex", [-c for c in res.capex]),
    ("Interest", [-i for i in res.interest]),
    ("Unlevered cash flow", res.cf_unlevered),
    ("Levered cash flow", res.cf_levered),
):
    cf_rows.append(
        {
            "k": label,
            **{
                f"y{t}": "–" if abs(v) < 0.05 else sek(v, 1)
                for t, v in zip(res.years, vals, strict=True)
            },
        }
    )
ui.table(cf_rows, cf_cols)

# sensitivity
eys = [a.exit_yield + x for x in (-0.005, -0.0025, 0.0, 0.0025, 0.005)]
gs = [a.rent_growth + x for x in (-0.01, -0.005, 0.0, 0.005, 0.01)]
grid = uw.sensitivity(a, eys, gs)
ui.render('<h3 class="hr-sub">Levered IRR by exit yield and NOI growth</h3>')
ui.table(
    [
        {
            "_class": "me" if i == 2 else None,
            "ey": ui.fmt_pct(ey, 2),
            **{f"g{j}": ui.fmt_pct(v, 1) for j, v in enumerate(row)},
        }
        for i, (ey, row) in enumerate(zip(eys, grid, strict=True))
    ],
    [ui.Col("ey", "Exit yield ↓ / growth →", "name")]
    + [ui.Col(f"g{j}", ui.fmt_pct(g, 1), "num") for j, g in enumerate(gs)],
)

notes = [
    ("Purchase price", "Company report" if use_company else f"NOI / entry yield. {ref_tip}"),
    (
        "NOI, year 1",
        "Company EBITDA"
        if use_company
        else f"Area × rent × (1 − vacancy) × margin. Rent: {rent_note} Margin: {margin_note}",
    ),
    ("NOI growth per year", "Riksbank inflation target; peers' long-term indexation."),
    ("Loan-to-value at entry", "JLL Nordic Outlook Spring 2026, p. 6: banks cautious above 60%."),
    ("All-in interest rate", debt_tip),
    ("Exit yield", "Entry reference + 25 bp."),
]
xls = uw.to_excel(a, f"Headroom underwriting: {seg_label}, {place}", notes)
st.download_button(
    "Export model to Excel",
    xls,
    file_name=f"headroom_underwriting_{kommun}_{segment}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
ui.source_line(
    "Interest-only debt at a fixed all-in rate, repaid at exit; exit value is the NOI of the "
    "year after the hold over the exit yield; no tax. The Excel file holds the same model as "
    "formulas, with the source of each default on its second sheet. A screening model, not a "
    "valuation."
)

source_tips()
