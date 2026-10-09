"""Issuer page: the key facts up front, the working behind expanders."""

from __future__ import annotations

import json

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from headroom.model.stress import breakeven_bp, shocked_icr
from ui import components as ui
from ui.data import get_data
from ui.labels import EVENT, SEGMENT, SEVERITY_STATE, TEST, TIER
from ui.plotly_template import BLUE, CONFIG, HIGH, INK

d = get_data()
ms = d.cfg["motivated_seller"]
bands = ms["bands"]
min_conf = d.cfg["extraction"]["min_confidence"]
stress_bp = ms["covenant"]["stress_bp"]

ranked = d.scores.sort("score", descending=True, nulls_last=True)
orgs = ranked["org_nr"].to_list()
names = dict(zip(ranked["org_nr"], ranked["name"], strict=True))
qp = st.query_params.get("org")
sel_col, _ = st.columns([4, 8])
org = sel_col.selectbox(
    "Issuer", orgs, index=orgs.index(qp) if qp in orgs else 0, format_func=lambda o: names[o]
)
if st.query_params.get("org") != org:
    st.query_params["org"] = org

co = d.company(org)
card = d.cards[org]
fins = (
    d.t["financials"]
    .filter((pl.col("org_nr") == org) & (pl.col("period_end") <= d.ref))
    .sort("period_end")
)
fin = fins.row(-1, named=True) if fins.height else None
prov = d.t["provenance"].filter(pl.col("org_nr") == org)
bonds = d.t["bond"].filter(pl.col("org_nr") == org).sort("maturity")
live_bonds = bonds.filter(pl.col("maturity") > d.ref)
events = (
    d.t["event"]
    .filter((pl.col("org_nr") == org) & (pl.col("date") <= d.ref))
    .sort("date", descending=True)
)


def field_conf(field: str) -> tuple[float | None, int | None, str | None]:
    if not fin:
        return None, None, None
    p = prov.filter((pl.col("period_end") == fin["period_end"]) & (pl.col("field") == field))
    if p.is_empty():
        return fin.get("confidence"), fin.get("page"), fin.get("source_url")
    r = p.row(0, named=True)
    return r["confidence"], r["page"], r["source_url"]


def fv(field: str, fmt) -> str:
    """Latest value, marked unverified when extraction confidence is low."""
    if not fin:
        return "–"
    conf, page, _ = field_conf(field)
    return ui.unverified(fmt(fin.get(field)), conf, min_conf, f"p. {page}" if page else None)


# --------------------------------------------------------------------------- hero

state = ui.band(card.score, bands["high"], bands["watch"])
score_txt = "–" if card.score is None else f"{card.score:.0f}"
gaps = len(card.flags)
ui.hero(
    f"{TIER[co['tier']]} · {co['org_nr']}",
    ui.esc(co["name"]),
    f'<span style="font-family:var(--display);font-size:1.6rem;color:var(--ink);line-height:1.3">'
    f"{ui.esc(card.opportunity)}.</span><br>{ui.esc(card.opportunity_rationale)}",
    [
        ui.Stat(
            score_txt,
            "/100" if card.score is not None else "",
            "Motivated-seller score"
            + (f", {gaps} component{'s' if gaps != 1 else ''} without data" if gaps else ""),
            state=state if card.score is not None else None,
        ),
        ui.Stat(
            "–" if card.fit is None else f"{card.fit:.0f}",
            "/100" if card.fit is not None else "",
            "Strategy fit"
            + ("" if card.fit is not None else ": segment and region split not yet known"),
            ink=True,
        ),
    ],
)

nxt = live_bonds.head(1).to_dicts()
next_txt = (
    f"{ui.fmt_date(nxt[0]['maturity'], 'long')}<br>"
    f'<span style="color:var(--muted)">{ui.fmt_sek_m(nxt[0]["nominal"])}</span>'
    if nxt
    else "No listed bonds"
)
period = (
    f'<br><span style="color:var(--muted)">{ui.fmt_date(fin["period_end"], "long")}</span>'
    if fin
    else ""
)
ui.facts(
    [
        ("Property value", fv("property_value", ui.fmt_sek_m) + period),
        ("LTV", fv("ltv", ui.fmt_pct) + period),
        ("Interest cover", fv("icr", lambda v: ui.fmt_x(v, 2)) + period),
        ("Next bond maturity", next_txt),
    ]
)

# --------------------------------------------------------------------------- 01 why


def basis(c) -> str:
    i = c.inputs
    if not c.available:
        return f'<span style="color:var(--muted)">Not scored: {ui.esc(c.note.lower() or "no data")}</span>'
    if c.key == "refinancing":
        return f"Maturities are {i['ratio']:.1f}x cash and undrawn facilities"
    if c.key == "covenant":
        return (
            f"{TEST.get(i['binding_test_stressed'], '')} covenant headroom "
            f"{ui.fmt_pct(i['min_headroom_stressed'], signed=True)} at +{i['stress_bp']}bp"
        )
    if c.key == "leverage":
        parts = []
        if "ltv" in i:
            parts.append(f"LTV {ui.fmt_pct(i['ltv'])}")
        if "icr" in i:
            parts.append(f"ICR {ui.fmt_x(i['icr'], 1)}")
        return ", ".join(parts)
    if c.key == "events":
        evs = i["events"]
        if not evs:
            return "No credit events"
        top = evs[0]
        more = f", and {len(evs) - 1} more" if len(evs) > 1 else ""
        return f"{EVENT.get(top['type'], top['type'])}, {top['age_days']} days ago{more}"
    if c.key == "market":
        return (
            f"Shares at {i['pnav']:.0%} of NAV"
            if "pnav" in i
            else f"Down {ui.fmt_pct(i.get('drawdown_52w'))} on the year"
        )
    return ""


ui.section(
    1, f"Why it scores {score_txt}" if card.score is not None else "Why it is not scored yet"
)
rows = [
    {
        "label": c.label,
        "score": f"{c.score:.0f}{ui.bar(c.score / 100)}" if c.available else "–",
        "basis": basis(c),
    }
    for c in card.components
]
ui.table(
    rows,
    [
        ui.Col("label", "Component"),
        ui.Col("score", "Score", "num", raw_html=True),
        ui.Col("basis", "Driver", "html"),
    ],
)

# --------------------------------------------------------------------------- 02 rate shock

with st.container(key="band_tint_shock"):
    ui.section(2, "Rate shock")
    if fin and fin.get("ebitda") and fin.get("interest_expense"):
        args = (fin["ebitda"], fin["interest_expense"], fin["gross_debt"], fin.get("fixed_share"))
        icr_covs = sorted(
            {r.threshold for r in card.covenants if r.test == "icr" and r.kind == "maintenance"}
        )
        lcol, _, rcol = st.columns([7, 0.5, 4.5])
        with rcol:
            bp = st.slider(
                "Rise in STIBOR, bp",
                0,
                d.cfg["rate_shock"]["max_bp"],
                stress_bp,
                step=d.cfg["rate_shock"]["step_bp"],
            )
            res = shocked_icr(*args, bp)
            breach = bool(icr_covs) and res.icr is not None and res.icr < icr_covs[0]
            be = breakeven_bp(*args, icr_covs[0]) if icr_covs else None
            cov_txt = f"Covenant {icr_covs[0]:.2f}x" if icr_covs else "No ICR covenant on file"
            ui.render(
                ui.stats_html(
                    [
                        ui.Stat(
                            "–" if res.icr is None else f"{res.icr:.2f}",
                            "x",
                            f"Interest cover at +{bp}bp. {cov_txt}",
                            ink=True,
                            state="high" if breach else None,
                        ),
                        ui.Stat(
                            "–" if be is None else f"{be:,.0f}",
                            "" if be is None else "bp",
                            "Already below the covenant at today's rates"
                            if be == 0
                            else "Rise that takes cover down to the covenant",
                        ),
                    ]
                )
            )
        with lcol:
            xs = list(range(0, d.cfg["rate_shock"]["max_bp"] + 1, 5))
            ys = [shocked_icr(*args, x).icr for x in xs]
            fig = go.Figure()
            fig.add_scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=INK, width=2),
                hovertemplate="+%{x}bp: %{y:.2f}x<extra></extra>",
            )
            for cv in icr_covs[:1]:
                fig.add_hline(y=cv, line=dict(color=BLUE, width=1, dash="dot"))
                fig.add_annotation(
                    x=xs[-1],
                    y=cv,
                    text=f"Covenant {cv:.2f}x",
                    showarrow=False,
                    xanchor="right",
                    yanchor="bottom",
                    font=dict(size=12.5, color=BLUE),
                )
            fig.add_scatter(
                x=[bp],
                y=[res.icr],
                mode="markers",
                hoverinfo="skip",
                marker=dict(symbol="square", size=10, color=HIGH if breach else INK),
            )
            fig.update_layout(
                height=320,
                showlegend=False,
                hovermode="closest",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(ticksuffix="bp"),
                yaxis=dict(ticksuffix="x", rangemode="tozero"),
            )
            st.plotly_chart(fig, config=CONFIG, width="stretch")
        ui.source_line(
            f"{res.assumption}. EBITDA held constant. Report dated "
            f"{ui.fmt_date(fin['period_end'])}. {d.source_label('financials')}."
        )
    else:
        ui.note("EBITDA or interest expense not reported, so the rate shock cannot be computed.")

# --------------------------------------------------------------------------- 03 events

notable = events.filter(pl.col("type") != "interim_report").head(3)
if notable.height:
    ui.section(3, "Recent events")
    ui.news(
        [
            {
                "date": e["date"],
                "kind": EVENT.get(e["type"], e["type"]),
                "state": SEVERITY_STATE.get(e["severity"], "ok"),
                "title": e["title"],
                "company_html": "",
                "source_html": ui.source_link(e["source_url"], e["source_name"] or "Source"),
            }
            for e in notable.to_dicts()
        ]
    )

# --------------------------------------------------------------------------- 04 view

ui.section(4 if notable.height else 3, "Investment view")
refi, covc = card.component("refinancing"), card.component("covenant")
facts_txt = []
if refi.available:
    facts_txt.append(
        f"Debt falling due over the next two years is {refi.inputs['ratio']:.1f} times "
        "available liquidity."
    )
if covc.available:
    facts_txt.append(
        f"Covenant headroom is {ui.fmt_pct(covc.inputs['min_headroom'])} today and "
        f"{ui.fmt_pct(covc.inputs['min_headroom_stressed'])} if rates rise "
        f"{covc.inputs['stress_bp']}bp."
    )
ui.render(
    f'<div class="hr-view"><p>{ui.esc(card.opportunity_rationale)}</p>'
    + (f"<p>{ui.esc(' '.join(facts_txt))}</p>" if facts_txt else "")
    + "</div>"
)


def memo_facts() -> list:
    """Numbered facts for the memo, each with its source. Built from this page's data only."""
    from headroom.extract.memo import Fact

    facts, n = [], 0

    def add(text: str, source: str | None) -> None:
        nonlocal n
        n += 1
        facts.append(Fact(f"S{n}", text, source or "snapshot"))

    add(f"{co['name']} (org. nr {co['org_nr']}) is a {TIER[co['tier']].lower()}.", co["source_url"])
    if card.score is not None:
        add(
            f"Motivated-seller score {card.score:.0f}/100; opportunity type assessed as "
            f"{card.opportunity}: {card.opportunity_rationale}",
            "Headroom scoring model",
        )
    for c in card.components:
        if c.available:
            add(f"{c.label} sub-score {c.score:.0f}/100. {basis(c)}", "Headroom scoring model")
    if fin:
        for key, label, fmt in (
            ("property_value", "Property value", ui.fmt_sek_m),
            ("ltv", "LTV", ui.fmt_pct),
            ("icr", "Interest cover", lambda v: ui.fmt_x(v, 2)),
            ("debt_due_12m", "Debt due within 12 months", ui.fmt_sek_m),
            ("cash", "Cash", ui.fmt_sek_m),
            ("undrawn_facilities", "Undrawn facilities", ui.fmt_sek_m),
            ("avg_rate", "Average interest rate", lambda v: ui.fmt_pct(v, 2)),
            ("fixed_share", "Fixed or hedged share of debt", ui.fmt_pct),
        ):
            if fin.get(key) is not None:
                _conf, page, url = field_conf(key)
                src = f"{url}, p. {page}" if page else url
                add(f"{label} {fmt(fin[key])} at {ui.fmt_date(fin['period_end'])}.", src)
    for b in live_bonds.to_dicts():
        add(
            f"Bond {b['isin']}: {ui.fmt_sek_m(b['nominal_sek'])}, maturity {ui.fmt_date(b['maturity'])}, "
            f"{'secured' if b['secured'] else 'unsecured'}.",
            b["source_url"],
        )
    for r in card.covenants:
        if r.kind == "maintenance" and r.headroom is not None:
            add(
                f"{TEST.get(r.test, r.test)} maintenance covenant {r.threshold}: headroom "
                f"{ui.fmt_pct(r.headroom, 1)}, {ui.fmt_pct(r.headroom_stressed, 1)} at +{stress_bp}bp.",
                r.source_url,
            )
    for e in notable.head(6).to_dicts():
        add(f"{ui.fmt_date(e['date'])}: {e['title']}", e["source_url"])
    return facts


from headroom.config import settings  # noqa: E402

if st.button(
    "Draft memo",
    disabled=not settings().llm_available or d.fictional,
    help="Writes a draft memo from the facts on this page. Every sentence cites a source.",
):
    from headroom.extract.memo import build_memo, check_citations, to_markdown

    facts = memo_facts()
    with st.status("Drafting", expanded=False):
        memo, _meta = build_memo(co["name"], facts)
    bad = check_citations(memo, facts)
    md = to_markdown(memo, facts, bad)
    st.session_state[f"memo_{org}"] = md
if st.session_state.get(f"memo_{org}"):
    with st.container(key="memo_box"):
        st.markdown(st.session_state[f"memo_{org}"])
        st.download_button(
            "Download memo",
            st.session_state[f"memo_{org}"].encode("utf-8"),
            file_name=f"memo_{org}.md",
            mime="text/markdown",
        )
ui.source_line(
    "Rule-based summary above; the draft memo is written by an LLM from cited facts only and "
    "is labelled draft. Not investment advice."
    + (" Memos are off in demo mode." if d.fictional else "")
)

# --------------------------------------------------------------------------- details

ui.render('<div style="height:3rem"></div>')
ui.render(ui.eyebrow("Details"))

with st.expander("Score calculation"):
    rows = [
        {
            "label": c.label,
            "w": ui.fmt_pct(c.weight),
            "ew": ui.fmt_pct(c.effective_weight) if c.available else "–",
            "score": f"{c.score:.0f}" if c.available else "–",
            "pts": f"{c.contribution:.1f}" if c.available else "–",
            "note": ui.esc(c.note) if c.note else "",
        }
        for c in card.components
    ]
    ui.table(
        rows,
        [
            ui.Col("label", "Component"),
            ui.Col("w", "Weight", "num"),
            ui.Col("ew", "Applied", "num"),
            ui.Col("score", "Score", "num"),
            ui.Col("pts", "Points", "num"),
            ui.Col("note", "Note", "html"),
        ],
        foot={"label": "Total", "ew": "100%", "pts": score_txt},
    )
    ui.source_line("Components without data get no weight; the others are scaled up pro rata.")

with st.expander("Covenants"):
    if card.covenants:
        rows = []
        for r in sorted(card.covenants, key=lambda r: r.kind != "maintenance"):
            pct = r.test in ("ltv", "equity_ratio")

            def f(v, pct=pct, test=r.test):
                return (
                    ui.fmt_pct(v) if pct else ui.fmt_x(v, 2) if test == "icr" else ui.fmt_sek_m(v)
                )

            hs = r.headroom_stressed
            mk = (
                "high"
                if hs is not None and hs < 0
                else "watch"
                if hs is not None and hs < 0.1
                else "ok"
            )
            rows.append(
                {
                    "test": f"{TEST.get(r.test, r.test)} {'≤' if r.test == 'ltv' else '≥'} "
                    + ui.unverified(f(r.threshold), r.confidence, min_conf),
                    "kind": r.kind.capitalize(),
                    "act": f(r.actual),
                    "hr": ui.fmt_pct(r.headroom, 1, signed=True),
                    "hrs": f"{ui.marker(mk)}{ui.fmt_pct(hs, 1, signed=True)}",
                    "src": ui.source_link(
                        r.source_url, f"{r.isin}, p. {r.page}" if r.page else r.isin
                    ),
                }
            )
        ui.table(
            rows,
            [
                ui.Col("test", "Test", "html"),
                ui.Col("kind", "Kind"),
                ui.Col("act", "Actual", "num"),
                ui.Col("hr", "Headroom", "num"),
                ui.Col("hrs", f"At +{stress_bp}bp", "num", raw_html=True),
                ui.Col("src", "Source", "html"),
            ],
        )
        ui.source_line("Reported ICR is a proxy for the covenant definition, which can differ.")
    else:
        ui.note("No bond covenants on file.")

with st.expander("Bonds"):
    if bonds.height:
        rows = [
            {
                "isin": b["isin"],
                "nominal": ui.fmt_sek_m(b["nominal"]),
                "mat": b["maturity"],
                "coupon": (
                    f"STIBOR + {b['margin_bp']:.0f}bp"
                    if b["coupon_type"] == "floating"
                    else f"{b['coupon_pct']:.2f}% fixed"
                    if b["coupon_pct"]
                    else "Fixed"
                ),
                "sec": "Secured" if b["secured"] else "Unsecured",
                "agent": b["trustee"],
                "src": ui.source_link(b["source_url"], "FIRDS"),
            }
            for b in bonds.to_dicts()
        ]
        ui.table(
            rows,
            [
                ui.Col("isin", "ISIN", "mono"),
                ui.Col("nominal", "Nominal", "num"),
                ui.Col("mat", "Maturity", "date", ui.fmt_date),
                ui.Col("coupon", "Coupon"),
                ui.Col("sec", "Security"),
                ui.Col("agent", "Agent"),
                ui.Col("src", "Source", "html"),
            ],
        )
    else:
        ui.note("No listed bonds.")

with st.expander("Financial history"):
    if fins.height:
        cols = st.columns(3, gap="large")
        for col, (key, title, fmt) in zip(
            cols,
            [
                ("ltv", "LTV", lambda v: f"{v * 100:.1f}%"),
                ("icr", "Interest cover", lambda v: f"{v:.2f}x"),
                ("property_value", "Property value, SEK m", lambda v: f"{v:,.0f}"),
            ],
            strict=True,
        ):
            s = fins.filter(pl.col(key).is_not_null())
            with col:
                ui.render(f'<div class="hr-eyebrow" style="margin:0">{ui.esc(title)}</div>')
                if s.is_empty():
                    ui.note("Not reported")
                    continue
                ys = s[key].to_list()
                fig = go.Figure(
                    go.Scatter(
                        x=s["period_end"].to_list(),
                        y=ys,
                        mode="lines",
                        line=dict(color=INK, width=1.75),
                        hovertemplate="%{x|%Y-%m-%d}<extra></extra>",
                    )
                )
                fig.add_annotation(
                    x=s["period_end"][-1],
                    y=ys[-1],
                    text=fmt(ys[-1]),
                    showarrow=False,
                    yshift=12,
                    xanchor="right",
                    font=dict(size=12, color=INK),
                )
                fig.update_layout(
                    height=170,
                    margin=dict(l=0, r=4, t=22, b=0),
                    hovermode="closest",
                    yaxis=dict(showticklabels=False),
                    xaxis=dict(tickformat="%Y", nticks=3),
                )
                st.plotly_chart(fig, config=CONFIG, width="stretch", key=f"fin_{key}")
        fields = [
            ("property_value", "Property value", ui.fmt_sek_m),
            ("net_debt", "Net debt", ui.fmt_sek_m),
            ("ltv", "LTV", ui.fmt_pct),
            ("icr", "ICR", lambda v: ui.fmt_x(v, 2)),
            ("ebitda", "EBITDA, LTM", ui.fmt_sek_m),
            ("interest_expense", "Net interest, LTM", ui.fmt_sek_m),
            ("avg_rate", "Average interest rate", lambda v: ui.fmt_pct(v, 2)),
            ("fixed_share", "Fixed or hedged share", ui.fmt_pct),
            ("equity_ratio", "Equity ratio", ui.fmt_pct),
            ("cash", "Cash", ui.fmt_sek_m),
            ("undrawn_facilities", "Undrawn facilities", ui.fmt_sek_m),
            ("debt_due_12m", "Debt due within 12 months", ui.fmt_sek_m),
            ("debt_due_24m", "Debt due within 24 months", ui.fmt_sek_m),
        ]
        rows = []
        for key, label, fmt in fields:
            conf, page, url = field_conf(key)
            have = fin.get(key) is not None
            rows.append(
                {
                    "label": label,
                    "v": fv(key, fmt),
                    "conf": f"{conf:.2f}" if have and conf is not None else "–",
                    "src": ui.source_link(url, f"Report, p. {page}" if page else "Report")
                    if have
                    else "–",
                }
            )
        ui.table(
            rows,
            [
                ui.Col("label", f"Reported at {ui.fmt_date(fin['period_end'])}"),
                ui.Col("v", "Value", "num", raw_html=True),
                ui.Col("conf", "Confidence", "num"),
                ui.Col("src", "Source", "html"),
            ],
        )
        ui.source_line(
            f"Values below confidence {min_conf:.2f} are marked unverified. "
            f"{d.source_label('financials')}."
        )
    else:
        ui.note("No financial statements on file.")

with st.expander(f"All events ({events.height})"):
    if events.height:
        rows = [
            {
                "date": e["date"],
                "type": f"{ui.marker(SEVERITY_STATE.get(e['severity'], 'ok'))}{ui.esc(EVENT.get(e['type'], e['type']))}",
                "title": e["title"],
                "src": ui.source_link(e["source_url"], e["source_name"] or "Source"),
            }
            for e in events.to_dicts()
        ]
        ui.table(
            rows,
            [
                ui.Col("date", "Date", "date", ui.fmt_date),
                ui.Col("type", "Type", "html"),
                ui.Col("title", "Event"),
                ui.Col("src", "Source", "html"),
            ],
        )
    else:
        ui.note("No events in the monitored sources.")

with st.expander("Company"):
    seg = json.loads(co["segment_mix"] or "{}")
    reg = json.loads(co["region_mix"] or "{}")
    ui.facts(
        [
            ("LEI", ui.esc(co["lei"] or "–")),
            ("Listing", ui.esc(co["listed_ticker"] or "Unlisted equity")),
            ("SNI", ui.esc(co["sni"] or "–")),
            ("Source", ui.source_link(co["source_url"], "Register")),
            (
                "Segments",
                ", ".join(
                    f"{SEGMENT.get(k, k)} {v:.0%}"
                    for k, v in sorted(seg.items(), key=lambda x: -x[1])
                )
                or "–",
            ),
            (
                "Regions",
                ", ".join(f"{k} {v:.0%}" for k, v in sorted(reg.items(), key=lambda x: -x[1]))
                or "–",
            ),
        ]
    )
