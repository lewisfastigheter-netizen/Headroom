"""Method: sources, coverage, how each score is built, limitations, as-of dates.

Every section has the same shape: a heading, one short paragraph, and tables, each
table under a small label. Numbers come from config/weights.yaml, so the page always
matches the scores.
"""

from __future__ import annotations

from headroom.model.location import COMPONENT_LABEL, COMPONENT_MEASURE
from headroom.store import private
from ui import components as ui
from ui.data import get_data

d = get_data()
cfg = d.cfg
ms = cfg["motivated_seller"]
lc = cfg.get("location", {})

# Columns shared by every score table, so they all read the same way.
SCORE_COLS = [
    ui.Col("part", "Part"),
    ui.Col("w", "Weight", "num"),
    ui.Col("measure", "Measure"),
    ui.Col("zero", "Scores 0"),
    ui.Col("full", "Scores 100"),
]


def sub(label: str) -> None:
    """Small label above a table."""
    ui.render(f'<div class="hr-sub">{ui.eyebrow(label)}</div>')


def part(name: str, weight: float, measure: str, zero: str, full: str) -> dict:
    return {"part": name, "w": f"{weight:.0%}", "measure": measure, "zero": zero, "full": full}


GITHUB = "https://github.com/lewisfastigheter-netizen/Headroom"

ui.page_head(
    "Method",
    "Headroom looks for Swedish property owners who may have to sell. It collects public data "
    "on every property company it can find, joins it on the organisation number, and ranks "
    "the companies on how stretched their balance sheets are.",
)
ui.render(
    '<div class="hr-prose" style="margin-top:1.6rem"><p>'
    "Built by William Lewis in Python. A data pipeline reads Bolagsverket's company register "
    "and machine-readable (iXBRL) annual reports, ESMA's bond register, newswire releases and "
    "SCB's statistics; key figures in PDF reports are read by a language model and stored with "
    "their page and a confidence score. Polars and DuckDB hold the data, scheduled GitHub "
    "Actions keep it current (the private-company scan runs four times a day), and the app is "
    f'written in Streamlit. The code is on <a href="{GITHUB}" target="_blank">GitHub</a>.'
    "</p><p>A personal research project, not affiliated with any investment firm and not "
    "investment advice. Below: how the scores are built, and where they can be wrong.</p></div>"
)

# --------------------------------------------------------------------------- companies

tiers = {r["tier"]: r["count"] for r in d.t["company"]["tier"].value_counts().to_dicts()}
ui.section(
    None,
    "Companies covered",
    "Companies are joined on the Swedish organisation number. How much is public depends on the type "
    "of company, and so does how much of the score can be computed.",
)
sub("Types of company")
ui.table(
    [
        {
            "t": "Listed property companies",
            "n": ui.fmt_num(tiers.get("listed", 0)),
            "p": "Quarterly reports and a share price",
        },
        {
            "t": "Bond issuers",
            "n": ui.fmt_num(tiers.get("bond", 0)),
            "p": "Interim reports and bondholder notices, required by the bond terms",
        },
        {
            "t": "Private companies",
            "n": ui.fmt_num(tiers.get("private", 0)),
            "p": "Annual reports only, often late",
        },
    ],
    [ui.Col("t", "Type"), ui.Col("n", "Companies", "num"), ui.Col("p", "What is public")],
)
scan = private.progress()
pct = scan["checked"] / scan["candidates"] if scan["candidates"] else 0
sub(
    f"Private-company scan: {scan['checked']:,} of {scan['candidates']:,} candidates checked "
    f"({pct:.1%}), last on {ui.fmt_date(scan['last_checked'], 'long')}"
)
ui.table(
    [
        {"r": label, "n": ui.fmt_num(scan["by_status"].get(k, 0))}
        for k, label in private.STATUS_LABEL.items()
        if scan["by_status"].get(k) or k != "no_ixbrl"
    ],
    [ui.Col("r", "Result"), ui.Col("n", "Companies", "num")],
)
ui.source_line(
    "Candidates are active aktiebolag in Bolagsverket's bulk file whose name or business description "
    "points to property. Each is checked for its SNI code and latest digitally filed annual report; "
    "those with at least SEK 20m of property join the list. The scan runs four times a day."
)

# --------------------------------------------------------------------------- how scores work

ui.section(
    None,
    "How every score is built",
    "Every score on Headroom, for companies and for places, follows the same two rules. "
    "First, each part turns one figure into 0–100 on a straight line between the value that scores 0 "
    "and the value that scores 100; anything beyond is clipped. Second, the score is the weighted "
    "average of the parts. A part without data is left out and the others count for more; nothing is "
    "guessed. The weights are judgement, not estimated, and live in <code>config/weights.yaml</code> "
    "so they can be challenged.",
)

# --------------------------------------------------------------------------- seller score

w, sz = ms["weights"], ms["size_sek_m"]
ui.section(
    None,
    "Motivated-seller score",
    "How likely is this owner to have to sell? The score rests mainly on leverage: loan-to-value "
    "and interest cover, as reported in the latest accounts. A smaller part gives more points to "
    f"companies with SEK {sz['band_low']:,}–{sz['band_high']:,}m of property, the deal size "
    "Headroom is aimed at. With data for less "
    f"than {ms['min_coverage']:.0%} of the weight a company is not scored, so property value alone "
    "is not enough.",
)
sub("Parts")
ui.table(
    [
        part(
            "Loan-to-value",
            w["ltv"],
            "Net debt ÷ property value (fair value where reported, otherwise book value)",
            f"{ms['ltv']['zero']:.0%} or less",
            f"{ms['ltv']['full']:.0%} or more",
        ),
        part(
            "Interest cover",
            w["icr"],
            "EBIT ÷ net interest expense",
            f"{ms['icr']['zero']:g}x or more",
            f"{ms['icr']['full']:g}x or less",
        ),
        part(
            "Property value",
            w["size"],
            "Full score inside the band, a straight fade to zero at the outer bounds",
            f"Below SEK {sz['outer_low']:,}m or above {sz['outer_high']:,}m",
            f"SEK {sz['band_low']:,}–{sz['band_high']:,}m",
        ),
    ],
    SCORE_COLS,
)
ui.source_line(
    "Bond covenants, maturities and credit events are still collected and shown on each company "
    "page, but do not change the score."
)

# --------------------------------------------------------------------------- locations


def _threshold(key: str, v: float) -> str:
    """A threshold in the unit people read it in."""
    if key in ("growth_5y", "growth"):
        return f"{v:+.0%} a year"
    if key == "rental_share":
        return f"{v:.0%}"
    if key == "bme":
        return {0: "Shortage", 1: "Balance", 2: "Surplus"}.get(round(v), f"{v:g}")
    if key == "node_distance":
        return f"{v:,.0f} km"
    if key == "catchment_100km":
        return f"{v / 1e6:,.1f} million"
    if key in ("logistics_lq", "job_hub"):
        return f"{v:g}x"
    if key == "housing_pressure":
        return f"{v:g} per home"
    if key == "sei_improvement":
        return f"{v:+g} points"
    if key == "young_inflow":
        return f"{v:+g} per 1,000"
    return f"{v:g}"


ui.section(
    None,
    "Location scores",
    "The Locations page scores places for rental housing and for logistics, with the same two rules "
    f"and four parts each; below {lc.get('min_coverage', 0.5):.0%} of the weight a place is not "
    "scored. Distances and catchments are straight lines from a population-weighted centre, an "
    "approximation of drive time. Police-listed vulnerable areas are flagged and do not change the "
    "score. The location scores do not affect the company scores.",
)
for key, title in (
    (
        "residential",
        "Residential, municipalities and counties: are people arriving faster than homes are built?",
    ),
    ("residential_regso", "Residential, areas: is the area growing, rented and improving?"),
    ("logistics", "Logistics: are there people within reach, and a logistics base to serve them?"),
):
    block = lc.get(key)
    if not block:
        continue
    sub(title)
    ui.table(
        [
            part(
                COMPONENT_LABEL.get(k, k),
                wt,
                COMPONENT_MEASURE.get(k, "")
                + (" (log scale)" if block[k].get("scale") == "log" else ""),
                _threshold(k, block[k]["zero"]),
                _threshold(k, block[k]["full"]),
            )
            for k, wt in block["weights"].items()
        ],
        SCORE_COLS,
    )

# --------------------------------------------------------------------------- limitations

ui.section(
    None,
    "Limitations",
    "Where the figures can mislead, and what to check before relying on them.",
)
LIMITS = [
    (
        "Book vs market value",
        "Private companies under K2/K3 carry property at cost, usually well below market value. "
        "Headroom uses the fair value from the notes where given, otherwise book value, and marks LTV "
        "on book value BOOK, as it overstates leverage.",
    ),
    (
        "Leverage only",
        "The score reads the balance sheet only. Debt maturities, covenants and credit events also "
        "drive sales; they are shown on the company pages but not scored.",
    ),
    (
        "Extraction",
        "An LLM can misread tables. Every extracted figure stores its page and confidence; figures "
        f"below {cfg['extraction']['min_confidence']:.0%} confidence are marked unverified.",
    ),
    (
        "Private companies",
        "Annual reports arrive up to seven months after the year end, so their scores rest on older "
        "data. Companies that file their annual report on paper or as a scanned PDF cannot be read "
        "and are not in the list.",
    ),
    (
        "Events",
        "An event published only on a company's own website, not on its newswire, can be missed.",
    ),
]

ui.table([{"t": a, "l": b} for a, b in LIMITS], [ui.Col("t", "Topic"), ui.Col("l", "Limitation")])

# --------------------------------------------------------------------------- as-of

meta = d.meta
ui.section(
    None,
    "Data as of",
    f"{'Fictional demo dataset. ' if d.fictional else ''}Scores are computed as of {ui.fmt_date(d.ref, 'long')}.",
)
ui.table(
    [{"t": t, "a": a} for t, a in (meta.get("as_of") or {}).items()],
    [ui.Col("t", "Table", "mono"), ui.Col("a", "As of", "date")],
)
src = meta.get("sources") or {}
if src:
    debt, eq = src.get("firds_debt", {}), src.get("firds_equity", {})
    ui.source_line(
        f"Bonds from ESMA FIRDS full files of {debt.get('publication_date', '–')}, listed shares from "
        f"those of {eq.get('publication_date', '–')}. {src.get('issuers_swedish', '–')} Swedish issuers "
        f"have bonds outstanding, of which {src.get('issuers_property', '–')} are property companies. "
        "EUR and NOK amounts are converted at the Riksbank mid rate of the snapshot date. "
        f"Snapshot generated {meta.get('generated_at', '–')}."
    )

# --------------------------------------------------------------------------- sources

ui.section(
    None,
    "Sources",
    "All requests identify the project, respect robots.txt and terms of use, and are rate-limited "
    "and cached. Only company data is collected, never data on private individuals.",
)
SOURCES = [
    ("ESMA FIRDS", "Bonds: ISIN, issuer, nominal, maturity, coupon; listed shares", "Connected"),
    ("GLEIF", "Issuer LEI to legal name and organisation number", "Connected"),
    (
        "Sveriges Riksbank",
        "Policy rate, SWESTR, government and mortgage bond yields, FX",
        "Connected",
    ),
    (
        "MFN and Cision",
        "Press releases, written procedures, waivers, disposals, report PDFs",
        "Connected",
    ),
    (
        "Company reports",
        "LTV, ICR, rates, hedging, maturities, NAV, read by an LLM with page and confidence",
        "Connected",
    ),
    ("Yahoo Finance", "Weekly share prices for listed companies", "Connected"),
    (
        "Bolagsverket",
        "Private companies: SNI codes and digitally filed annual reports",
        "Connected",
    ),
    (
        "SCB",
        "Locations: population, housing, income, jobs, commuting, borders, 1 km grid",
        "Connected",
    ),
    ("Kolada", "Locations: housing-market assessment, similar municipalities", "Connected"),
    (
        "Polisen, OpenStreetMap, Wikidata",
        "Locations: vulnerable areas; ports, terminals, cargo airports",
        "Saved files",
    ),
    ("Svensk Mäklarstatistik", "Flat prices by area", "Not used: licence forbids it"),
    ("Post- och Inrikes Tidningar", "Insolvency notices", "Not used: blocks automated access"),
]

ui.table(
    [{"s": a, "u": b, "st": c} for a, b, c in SOURCES],
    [ui.Col("s", "Source"), ui.Col("u", "Used for"), ui.Col("st", "Status", "dim")],
)
