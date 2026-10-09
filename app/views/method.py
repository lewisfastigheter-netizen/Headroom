"""Method: sources, definitions, weights, limitations, as-of dates."""

from __future__ import annotations

from ui import components as ui
from ui.data import get_data

d = get_data()
ms = d.cfg["motivated_seller"]
fit = d.cfg["strategy_fit"]

ui.hero(
    "Method",
    "How the screen is built, and where it can be wrong.",
    "Headroom is a personal research project. It is not affiliated with any investment firm and is "
    "not investment advice. Every figure on screen carries a source and an as-of date. Extracted "
    "figures below the confidence threshold are marked unverified.",
)

# --------------------------------------------------------------------------- sources

ui.section(1, "Sources")
sources = [
    (
        "ESMA FIRDS",
        "Debt instruments: ISIN, issuer LEI, nominal, maturity, coupon, venue; listed shares",
        "Weekly full files (FULINS_D, FULINS_E)",
        "Connected",
    ),
    (
        "GLEIF",
        "LEI to legal name, country and Swedish organisation number",
        "Public REST API",
        "Connected",
    ),
    (
        "Sveriges Riksbank",
        "Policy rate, SWESTR, 3-month treasury bill, FX",
        "SWEA and SWESTR APIs",
        "Connected",
    ),
    (
        "MFN and Cision",
        "Releases, written procedures, waivers, disposals; report PDFs",
        "MFN company search (matched on org. number) and release pages; Cision pages",
        "Connected",
    ),
    (
        "Company reports",
        "LTV, ICR, rates, hedging, maturities, NAV; page and confidence per field",
        "Report PDFs read by an LLM with structured output",
        "Connected",
    ),
    (
        "Yahoo Finance via yfinance",
        "Weekly share prices for listed companies",
        "Unofficial endpoint",
        "Connected",
    ),
    (
        "Bolagsverket",
        "SNI codes, digitally filed annual reports (iXBRL), proceedings; private-AB universe",
        "Värdefulla datamängder API (OAuth2) and bulk file",
        "Code ready; runs once API credentials are set",
    ),
    (
        "Post- och Inrikes Tidningar",
        "Bankruptcy, reconstruction and liquidation notices",
        "Website blocks automated access",
        "Not scraped; Bolagsverket and releases cover these events",
    ),
]
ui.table(
    [{"s": a, "w": b, "a": c, "m": m} for a, b, c, m in sources],
    [
        ui.Col("s", "Source"),
        ui.Col("w", "Used for"),
        ui.Col("a", "Access"),
        ui.Col("m", "Connected in", "dim"),
    ],
)
ui.note(
    "All requests identify the project in the user agent, respect robots.txt and terms of use, "
    "are rate-limited and cached. Only company data is collected; no data on private individuals."
)

# --------------------------------------------------------------------------- tiers

ui.section(2, "Universe")
ui.render("""<div class="hr-prose">
<p>Companies are joined on the Swedish organisation number across three tiers.</p>
<p><b>Listed property companies</b> report quarterly KPIs and have a share price, so P/NAV and drawdown are available.
<b>Bond issuers</b> without listed equity publish interim reports and bondholder notices because their bond terms require it;
they are the bridge between the listed and private markets. <b>Private ABs</b> file annual reports and appear in insolvency notices, nothing more.</p>
</div>""")

# --------------------------------------------------------------------------- score

ui.section(3, "Motivated-seller score")
w = ms["weights"]
r, c, lv, ev, mk = ms["refinancing"], ms["covenant"], ms["leverage"], ms["events"], ms["market"]
rows = [
    {
        "k": "Refinancing pressure",
        "w": ui.fmt_pct(w["refinancing"]),
        "d": f"Debt due within 12 months plus {r['weight_12_24m']:.0%} of debt due in months 13–24, divided by cash and "
        f"undrawn facilities. Scored on a log scale: {r['ratio_zero']}x scores 0, {r['ratio_full']}x or more scores 100.",
    },
    {
        "k": "Covenant headroom",
        "w": ui.fmt_pct(w["covenant"]),
        "d": f"Tightest maintenance test across ICR, LTV, equity ratio and minimum liquidity. Scored at base rates and with ICR "
        f"re-tested at +{c['stress_bp']}bp, averaged {1 - c['stress_weight']:.0%}/{c['stress_weight']:.0%}. "
        f"{c['headroom_zero']:.0%} headroom or more scores 0; at or through the covenant scores 100.",
    },
    {
        "k": "Leverage and coverage",
        "w": ui.fmt_pct(w["leverage"]),
        "d": f"Average of LTV ({lv['ltv']['zero']:.0%} → 0, {lv['ltv']['full']:.0%} → 100), ICR ({lv['icr']['zero']}x → 0, "
        f"{lv['icr']['full']}x → 100) and net debt/EBITDA ({lv['net_debt_ebitda']['zero']}x → 0, {lv['net_debt_ebitda']['full']}x → 100).",
    },
    {
        "k": "Events",
        "w": ui.fmt_pct(w["events"]),
        "d": f"Points per event, halving every {ev['half_life_days']} days, summed and capped at {ev['cap']}.",
    },
    {
        "k": "Market",
        "w": ui.fmt_pct(w["market"]),
        "d": f"Listed only. Average of P/NAV discount ({mk['pnav_discount']['full']:.0%} discount → 100) and 52-week drawdown "
        f"({mk['drawdown_52w']['full']:.0%} → 100).",
    },
]
ui.table(rows, [ui.Col("k", "Component"), ui.Col("w", "Weight", "num"), ui.Col("d", "Definition")])
ui.note(
    "<b>Missing data.</b> A component without data is not scored. Its weight is redistributed pro rata across the "
    "components that are, and the company is flagged. Nothing is imputed. Market is not a gap for unlisted companies."
)

ev_rows = [
    {"t": k.replace("_", " ").capitalize(), "p": str(v)} for k, v in ev["severity"].items() if v
]
ui.render('<div style="height:2rem"></div>')
ui.render(ui.eyebrow("Event points before decay"))
ui.table(ev_rows, [ui.Col("t", "Event"), ui.Col("p", "Points", "num")])

# --------------------------------------------------------------------------- fit

ui.section(4, "Strategy fit and opportunity type")
fw = fit["weights"]
size = fit["size_sek_m"]
ui.render(f"""<div class="hr-prose">
<p>A separate 0–100 score for fit with a value-add mandate in Nordic residential, light industrial and logistics property.
It does not affect the motivated-seller score.</p>
<ul>
<li><b>Segment exposure, {fw["segment"]:.0%}.</b> Share of property value in {", ".join(s.replace("_", " ") for s in fit["target_segments"])}.</li>
<li><b>Growth regions, {fw["region"]:.0%}.</b> Share of property value in {", ".join(fit["growth_regions"])}.</li>
<li><b>Deal size, {fw["size"]:.0%}.</b> Full score for property value between SEK {size["band_low"]:,}m and {size["band_high"]:,}m,
fading to zero at SEK {size["outer_low"]:,}m and {size["outer_high"]:,}m.</li>
</ul>
<p>The opportunity type is a rule-based label: <i>bondholder-led wind-down</i> where bond terms have been reopened and the score is high;
<i>portfolio</i> or <i>single-asset sale</i> after insolvency events or by balance-sheet size; <i>sale-leaseback</i> for owner-occupiers
outside real estate; <i>recapitalisation</i> for high LTV with thin covenant headroom; <i>JV</i> for listed companies trading well below NAV.
The rules are in <code>src/headroom/model/scoring.py</code>.</p>
</div>""")

# --------------------------------------------------------------------------- shock

ui.section(5, "Rate shock")
ui.render("""<div class="hr-prose">
<p><code>shocked ICR = EBITDA / (net interest + floating debt × Δ)</code>, where floating debt is gross debt times one minus the
reported fixed or hedged share. If the hedged share is not reported, all debt is treated as floating, and the issuer page says so.
EBITDA is held constant and fixed-rate debt maturing during the period is not repriced, so the shock understates pressure for
issuers with near-term fixed-rate maturities.</p></div>""")

# --------------------------------------------------------------------------- limitations

ui.section(6, "Limitations")
ui.render("""<div class="hr-prose"><ul>
<li><b>Market value vs book value.</b> Companies reporting under IFRS value investment property at fair value (IAS 40), and LTV uses that.
Private companies under K2/K3 carry property at cost less depreciation, usually well below market value. Headroom uses the fair value
disclosed in the notes when the annual report has one, otherwise the book value, and labels it: <i>Market value</i> (stated as fair or market value),
<i>Market value (IFRS)</i> (basis not stated next to the figure, presumed fair value) or <i>Book value</i>. LTV on book value is flagged BOOK, as it overstates leverage.</li>
<li>Covenant definitions differ between bonds (rolling 12-month or quarterly ICR, LTV on market or book value). Reported KPIs are a proxy for the defined test.</li>
<li>Bank facilities usually carry tighter covenants than bonds and are rarely public. Covenant headroom is measured against bond terms only.</li>
<li>LLM extraction can misread tables. Each extracted field stores page and confidence; low-confidence values are shown as unverified and should be checked against the source.</li>
<li>Private ABs report annually and late. Their scores rest on older data and lack covenant and market components.</li>
<li>Press-release coverage depends on the issuer's newswire. An event published only on an issuer's website can be missed.</li>
<li>Weights are judgement, not estimated. They are exposed in <code>config/weights.yaml</code> so they can be challenged.</li>
</ul></div>""")

# --------------------------------------------------------------------------- as-of

ui.section(7, "Data as of")
meta = d.meta
rows = [{"t": t, "a": a} for t, a in (meta.get("as_of") or {}).items()]
ui.table(rows, [ui.Col("t", "Table", "mono"), ui.Col("a", "As of", "date")])
src = meta.get("sources") or {}
if src:
    debt, eq = src.get("firds_debt", {}), src.get("firds_equity", {})
    rules = d.t["company"]["universe_rule"].value_counts().sort("count", descending=True)
    rule_txt = ", ".join(f"{r['universe_rule']} {r['count']}" for r in rules.to_dicts())
    ui.render('<div style="height:1.5rem"></div>')
    ui.render(f"""<div class="hr-prose">
<p><b>Bond universe.</b> ESMA FIRDS full debt files of {debt.get("publication_date", "–")}
({", ".join(debt.get("files", []))}). Equity listings from the FIRDS equity files of
{eq.get("publication_date", "–")}. Issuer identity from GLEIF.</p>
<p>{src.get("issuers_swedish", "–")} Swedish issuers have bonds outstanding; {src.get("issuers_property", "–")}
are treated as property companies ({rule_txt}). Until Bolagsverket industry codes are connected, the
property filter reads the legal name and a seed list in <code>config/universe_seed.yaml</code>.
Every issuer reviewed, property or not, is saved in <code>issuer_audit.parquet</code> for checking.</p>
<p><b>Currencies.</b> Nominal amounts in EUR and NOK are converted to SEK at the Riksbank mid rate of
the snapshot date. Bond issue dates are the first trading dates reported to FIRDS.</p>
</div>""")
ui.source_line(
    f"Snapshot mode: {d.mode}. Generated {meta.get('generated_at', '–')}. "
    f"{'Fictional demo dataset. ' if d.fictional else ''}Reference date for scoring: {d.ref}."
)
