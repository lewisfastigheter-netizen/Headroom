"""Issuer page: the key facts up front, the working behind expanders."""

from __future__ import annotations

import json

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from ui import components as ui
from ui.data import get_data
from ui.labels import EVENT, SEGMENT, SEVERITY_STATE, TEST, TIER
from ui.plotly_template import CONFIG, INK

d = get_data()
ms = d.cfg["motivated_seller"]
bands = ms["bands"]
min_conf = d.cfg["extraction"]["min_confidence"]
size_band = ms["size_sek_m"]

ranked = d.scores.sort("score", descending=True, nulls_last=True)
orgs = ranked["org_nr"].to_list()
names = dict(zip(ranked["org_nr"], ranked["name"], strict=True))
# The company comes from the link or the search box in the header (?org=...).
qp = st.query_params.get("org")
org = qp if qp in orgs else orgs[0]
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
gaps = sum(not c.available for c in card.components)


def reading() -> str:
    """One plain sentence on what drives the score."""
    lev = []
    ltv, icr, sz = (card.component(k) for k in ("ltv", "icr", "size"))
    if ltv.available:
        lev.append(f"LTV of {ui.fmt_pct(ltv.inputs['ltv'])}")
    if icr.available:
        lev.append(f"interest cover of {ui.fmt_x(icr.inputs['icr'], 1)}")
    band_txt = f"SEK {size_band['band_low']:,}–{size_band['band_high']:,}m"
    parts = []
    if lev:
        parts.append(" and ".join(lev))
    if sz.available:
        v = sz.inputs["property_value"]
        where = (
            "inside"
            if size_band["band_low"] <= v <= size_band["band_high"]
            else "below"
            if v < size_band["band_low"]
            else "above"
        )
        parts.append(f"property worth {ui.fmt_sek_m(v)}, {where} the {band_txt} target band")
    if not parts:
        return "No LTV, interest cover or property value reported yet."
    txt = "; ".join(parts)
    return txt[:1].upper() + txt[1:] + "."


ui.hero(
    f"{TIER[co['tier']]} · {co['org_nr']}",
    ui.esc(co["name"]),
    ui.esc(reading()),
    [
        ui.Stat(
            score_txt,
            "/100" if card.score is not None else "",
            "Motivated-seller score"
            + (f", {gaps} part{'s' if gaps != 1 else ''} without data" if gaps else ""),
            state=state if card.score is not None else None,
        ),
    ],
)

# Where the seller is in a process: a sale agreed or under way, a bondholder process with no
# sale announced yet, or nothing announced. Read from the dated events and their titles.
SALE_WORDS = r"(?i)dispos|divest|sale of|sells|sold|agreement to sell|avyttr|försälj|säljer|sålt"
recent_ev = events.filter(pl.col("date") >= pl.lit(d.ref) - pl.duration(days=365))
sale_ev = recent_ev.filter(pl.col("title").str.contains(SALE_WORDS))
bond_ev = recent_ev.filter(
    pl.col("type").is_in(["written_procedure", "waiver", "interest_deferral", "reconstruction"])
)
if sale_ev.height:
    e = sale_ev.row(0, named=True)
    status = ("Sale agreed or under way", "high", e)
elif bond_ev.height:
    e = bond_ev.row(0, named=True)
    status = ("Bondholder process, no sale announced", "watch", e)
else:
    status = ("No process announced", None, None)

nxt = live_bonds.head(1).to_dicts()
next_txt = (
    f"{ui.fmt_date(nxt[0]['maturity'], 'long')}<br>"
    f'<span style="color:var(--muted)">{ui.fmt_sek_m(nxt[0]["nominal"])}</span>'
    if nxt
    else "No listed bonds"
)
if nxt and bond_ev.height:
    wp_date = bond_ev["date"][0]
    next_txt += ui.flag(
        "Terms amended",
        f"Bond terms were changed by written procedure or waiver on {wp_date}; the "
        "maturity registered in FIRDS may no longer apply. Check the latest notice.",
    )
period = (
    f'<br><span style="color:var(--muted)">{ui.fmt_date(fin["period_end"], "long")}</span>'
    if fin
    else ""
)
ui.facts(
    [
        (
            "Property value",
            fv("property_value", ui.fmt_sek_m)
            + ui.basis_tag((fin or {}).get("value_basis"))
            + period,
        ),
        (
            "LTV" + (" (book value)" if (fin or {}).get("value_basis") == "book_value" else ""),
            fv("ltv", ui.fmt_pct) + period,
        ),
        ("Interest cover", fv("icr", lambda v: ui.fmt_x(v, 2)) + period),
        ("Next bond maturity", next_txt),
    ]
)
label, st_state, ev = status
ev_html = (
    f" Latest: {ui.esc(ev['title'])} ({ui.fmt_date(ev['date'])}, "
    f"{ui.source_link(ev['source_url'], ev['source_name'] or 'source')})."
    if ev
    else ""
)
ui.render(
    f'<div class="hr-note" style="margin-top:1.4rem">{ui.marker(st_state) if st_state else ""}'
    f"<b>Process: {ui.esc(label)}.</b>{ev_html}</div>"
)

# --------------------------------------------------------------------------- 01 why


def basis(c) -> str:
    """One line on what drives a part, in plain words."""
    i = c.inputs
    if not c.available:
        why = ui.esc((c.note[:1].lower() + c.note[1:]) if c.note else "no data")
        return f'<span style="color:var(--muted)">Not scored: {why}</span>'
    if c.key == "ltv":
        return f"LTV {ui.fmt_pct(i['ltv'])}"
    if c.key == "icr":
        return f"Interest cover {ui.fmt_x(i['icr'], 1)}"
    if c.key == "size":
        return (
            f"Property value {ui.fmt_sek_m(i['property_value'])}; target band "
            f"SEK {size_band['band_low']:,}–{size_band['band_high']:,}m"
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
        ui.Col("label", "Part"),
        ui.Col("score", "Score", "num", raw_html=True),
        ui.Col("basis", "Driver", "html"),
    ],
)

# --------------------------------------------------------------------------- 02 events

notable = events.filter(pl.col("type") != "interim_report").head(3)
if notable.height:
    ui.section(2, "Recent events")
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

# --------------------------------------------------------------------------- 03 memo

ui.section(3 if notable.height else 2, "Draft memo")


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
        add(f"Motivated-seller score {card.score:.0f}/100. {reading()}", "Headroom scoring model")
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
                f"{ui.fmt_pct(r.headroom, 1)}.",
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
    "The draft memo is written by an LLM from the cited facts on this page only and is "
    "labelled draft. Not investment advice."
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
            "note": ui.esc(c.note),
        }
        for c in card.components
    ]
    ui.table(
        rows,
        [
            ui.Col("label", "Part"),
            ui.Col("w", "Weight", "num"),
            ui.Col("ew", "Applied", "num"),
            ui.Col("score", "Score", "num"),
            ui.Col("pts", "Points", "num"),
            ui.Col("note", "Note", "html"),
        ],
        foot={"label": "Total", "ew": "100%", "pts": score_txt},
    )
    ui.source_line(
        "Score = weighted average of the parts. Parts without data are left out and the "
        "others count for more (Applied)."
    )

with st.expander("Covenants"):
    if card.covenants:
        rows = []
        for r in sorted(card.covenants, key=lambda r: r.kind != "maintenance"):
            pct = r.test in ("ltv", "equity_ratio")

            def f(v, pct=pct, test=r.test):
                return (
                    ui.fmt_pct(v) if pct else ui.fmt_x(v, 2) if test == "icr" else ui.fmt_sek_m(v)
                )

            hs = r.headroom
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
                    "hr": f"{ui.marker(mk)}{ui.fmt_pct(hs, 1, signed=True)}",
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
                ui.Col("hr", "Headroom", "num", raw_html=True),
                ui.Col("src", "Source", "html"),
            ],
        )
        ui.source_line(
            "Reported ICR is a proxy for the covenant definition, which can differ. Covenants "
            "are shown for reference and do not feed the score."
        )
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
