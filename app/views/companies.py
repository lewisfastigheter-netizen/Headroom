"""Companies: every property company in the universe, ranked by seller score."""

from __future__ import annotations

import io
import json

import polars as pl
import streamlit as st

from headroom.model import geo, valuation
from headroom.store import private
from headroom.store.private import group_of
from ui import components as ui
from ui.data import get_data
from ui.labels import SEGMENT, TIER

d = get_data()
min_conf = d.cfg["extraction"]["min_confidence"]

ACCOUNTING = {
    "ifrs": "IFRS",
    "k3_fair": "K3, fair value in notes",
    "book": "K2/K3, book value",
}
# The part of each label shown in italics (CSS cannot style part of a plain-text label,
# so the italic part is drawn with ::after on the menu item and the selected tag).
ACCOUNTING_ITALIC = {
    "k3_fair": ("K3,\\00a0", "fair value in notes"),
    "book": ("K2/K3,\\00a0", "book value"),
}

latest = (
    d.t["financials"]
    .filter(pl.col("period_end") <= d.ref)
    .sort("period_end")
    .group_by("org_nr")
    .agg(pl.all().last())
)
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
    # Listed companies and bond issuers report under IFRS (fair value). Private companies
    # follow K2/K3: book value, unless the annual report discloses fair value in a note.
    pl.when(pl.col("tier") != "private")
    .then(pl.lit("ifrs"))
    .when(pl.col("value_basis") == valuation.FAIR)
    .then(pl.lit("k3_fair"))
    .when(pl.col("value_basis") == valuation.BOOK)
    .then(pl.lit("book"))
    .otherwise(None)
    .alias("accounting"),
)

# Single-asset subsidiaries of listed groups (named after the parent) are not owners a
# buyer can approach on their own: tagged with the group and hidden unless asked for.
df = df.with_columns(
    pl.when(pl.col("tier") == "private")
    .then(pl.col("name").map_elements(group_of, return_dtype=pl.Utf8))
    .otherwise(None)
    .alias("group")
)

if d.fictional:
    ui.page_head(
        "Companies",
        "Property companies, ranked on a 0–100 seller score: how likely the owner is to have to "
        "sell. Method on the Method page.",
    )
else:
    # The private-company scan in one row: every company checked, then what became of them.
    # The four outcomes add up to the total; each has a swatch, a reason and its share.
    scan = private.progress()
    by = scan["by_status"]
    checked = scan["checked"]
    kept = by.get("kept", 0)
    ui.page_head(
        "Companies",
        f"Headroom has checked {ui.fmt_num(checked)} Swedish companies at Bolagsverket. "
        f"{ui.fmt_num(kept)} of them own SEK 20m or more of property and are ranked below, "
        "together with the listed companies and bond issuers.",
    )
    outcomes = [
        ("s3", "Not a property company", "Main SNI code is not 68, real estate", "not_real_estate"),
        (
            "s2",
            "No digitally filed annual report",
            "Filed on paper or as a scanned PDF",
            "no_digital_report",
        ),
        ("s2", "Annual report not machine-readable", "Filed without tagged figures", "no_ixbrl"),
        (
            "s1",
            "Under SEK 20m of property",
            "Property company, too little property",
            "below_threshold",
        ),
        ("in", "In Headroom", "SEK 20m or more of property", "kept"),
    ]
    cells = [
        '<div class="c total"><div class="l">Checked at Bolagsverket'
        "<small>Companies whose name or business points to property</small></div>"
        f'<div class="v">{ui.fmt_num(checked)}</div><div class="p">100%</div></div>'
    ]
    for sw, label, why, key in outcomes:
        n = by.get(key, 0)
        if not n and key != "kept":
            continue
        share = n / checked if checked else 0
        cells.append(
            f'<div class="c {"in" if key == "kept" else ""}" '
            f'title="{ui.esc(f"{label}: {n:,} companies, {share:.1%} of those checked")}">'
            f'<div class="l"><span class="sw {sw}"></span>{ui.esc(label)}'
            f"<small>{ui.esc(why)}</small></div>"
            f'<div class="v">{ui.fmt_num(n)}</div><div class="p">{share:.1%}</div></div>'
        )
    pending = scan["candidates"] - checked if scan["candidates"] else 0
    foot = (
        f"{ui.fmt_num(pending)} of {ui.fmt_num(scan['candidates'])} candidates still to check. "
        if pending > 0
        else ""
    )
    ui.render(
        f'<div class="hr-scan" style="--cols:{len(cells) - 1}">'
        + "".join(cells)
        + "</div>"
        + f'<div class="hr-source">{foot}Each company is checked in this order and counted at '
        "the first step it fails, so the four outcomes add up to the total. Shares are of all "
        "companies checked.</div>"
    )

# --------------------------------------------------------------------------- filters

ui.section(None, "Ranking")


def sv_key(s: str) -> str:
    """Swedish alphabetical order: Å, Ä, Ö after Z (Python's default puts Ä before Å)."""
    return s.casefold().translate(str.maketrans({"å": "{", "ä": "|", "ö": "}", "é": "e", "ü": "y"}))


counties = sorted(
    pl.read_csv(geo.GEO / "kommuner.csv", schema_overrides={"kommun_code": pl.Utf8})["county"]
    .unique()
    .to_list(),
    key=sv_key,
)

# A county or city picked in the header search arrives as ?county=...&city=...
qp_county, qp_city = st.query_params.get("county"), st.query_params.get("city")
if qp_county or qp_city:
    st.session_state["f_county"] = qp_county if qp_county in counties else "All counties"
    if qp_city and qp_county in counties:
        st.session_state[f"city_{qp_county}"] = qp_city
    for k in ("county", "city"):
        st.query_params.pop(k, None)

state = st.session_state
type_sel: list[str] = state.get("f_type") or []
# Listed companies and bond issuers all report under IFRS, so the accounting filter is only
# offered when private companies can be in the list.
show_acc = not type_sel or "private" in type_sel
acc_sel: list[str] = (state.get("f_acc") or []) if show_acc else []
county = state.get("f_county", "All counties")
city = state.get(f"city_{county}", "All cities")


show_groups = bool(state.get("f_groups"))


def apply(frame: pl.DataFrame, skip: str = "") -> pl.DataFrame:
    """Filter by every current choice except `skip` (used to count each option)."""
    if not show_groups:
        frame = frame.filter(pl.col("group").is_null())
    if type_sel and skip != "type":
        frame = frame.filter(pl.col("tier").is_in(type_sel))
    if acc_sel and skip != "acc":
        frame = frame.filter(pl.col("accounting").is_in(acc_sel))
    if county != "All counties" and skip not in ("county", "city"):
        frame = frame.filter(pl.col("county") == county)
    if city != "All cities" and skip != "city":
        frame = frame.filter(pl.col("city") == city)
    return frame


def present(frame: pl.DataFrame, col: str) -> set:
    return set(frame[col].drop_nulls().to_list())


styles: list[str] = []


def grey_empty(key: str, options: list, have: set, offset: int = 0) -> None:
    """Options with no companies (given the other filters) are shown in light grey."""
    for i, o in enumerate(options):
        if o not in have:
            styles.append(
                f'body:has(.st-key-{key} input:focus) [role="option"][data-key="{i + offset}"]'
                " { color: var(--muted) !important; }"
            )


filters = ["type", *(["acc"] if show_acc else []), "county", "city"]
cols = dict(zip(filters, st.columns(len(filters), gap="large"), strict=True))

cols["type"].multiselect(
    "Company type",
    list(TIER),
    format_func=TIER.get,
    placeholder="All company types",
    key="f_type",
)
grey_empty("f_type", list(TIER), present(apply(df, "type"), "tier"))
if show_acc:
    cols["acc"].multiselect(
        "Accounting standards",
        list(ACCOUNTING),
        format_func=ACCOUNTING.get,
        placeholder="All standards",
        key="f_acc",
    )
    grey_empty("f_acc", list(ACCOUNTING), present(apply(df, "acc"), "accounting"))
    for i, k in enumerate(ACCOUNTING):
        if k in ACCOUNTING_ITALIC:
            head, tail = ACCOUNTING_ITALIC[k]
            styles.append(
                f'body:has(.st-key-f_acc input:focus) [role="option"][data-key="{i}"] [data-item-hl],'
                f' .st-key-f_acc [data-tag][aria-label="{ACCOUNTING[k]}"] span[title]'
                f" {{ font-size: 0 !important; }}"
                f' body:has(.st-key-f_acc input:focus) [role="option"][data-key="{i}"] [data-item-hl]::before,'
                f' .st-key-f_acc [data-tag][aria-label="{ACCOUNTING[k]}"] span[title]::before'
                f' {{ content: "{head}"; font-size: 14px; }}'
                f' body:has(.st-key-f_acc input:focus) [role="option"][data-key="{i}"] [data-item-hl]::after,'
                f' .st-key-f_acc [data-tag][aria-label="{ACCOUNTING[k]}"] span[title]::after'
                f' {{ content: "{tail}"; font-size: 14px; font-style: italic; }}'
            )
cols["county"].selectbox("County", ["All counties", *counties], key="f_county")
grey_empty("f_county", counties, present(apply(df, "county"), "county"), offset=1)
city_options = sorted(present(df.filter(pl.col("county") == county), "city"), key=sv_key)
cols["city"].selectbox(
    "City", ["All cities", *city_options], disabled=county == "All counties", key=f"city_{county}"
)
grey_empty(f"city_{county}".replace(" ", "-"), city_options, present(apply(df, "city"), "city"), 1)
if styles:
    ui.render("<style>" + "\n".join(styles) + "</style>")

n_sub = df.filter(pl.col("group").is_not_null()).height
if n_sub:
    st.checkbox(
        f"Show {n_sub} single-asset subsidiaries of listed groups",
        key="f_groups",
        help="Private companies named after a listed parent (config/groups.yaml). They are "
        "hidden by default: the parent decides whether they are sold.",
    )
f = apply(df)
nb = (
    d.t["bond"]
    .filter((pl.col("maturity") > d.ref) & (pl.col("maturity").dt.year() < 2100))  # skip perpetuals
    .group_by("org_nr")
    .agg(pl.col("maturity").min().alias("next_maturity"))
)
# Ties (many companies reach 100) are broken by the higher LTV, then the lower ICR.
f = f.join(nb, on="org_nr", how="left").sort(
    ["score", "ltv", "icr"], descending=[True, True, False], nulls_last=True
)

# --------------------------------------------------------------------------- table

# With an accounting filter every row has the same basis, so the BOOK flag adds nothing.
show_basis = not acc_sel


def ratio(a: float | None, b: float | None, fmt) -> str:
    return fmt(a / b) if a is not None and b else "–"


CHECK = "Outside the range a property company's accounts can produce; the figure is probably misread. Check the source."


def checked() -> str:
    return "–" + ui.flag("Check", CHECK)


def icr_html(v: float | None, conf: float | None) -> str:
    if v is not None and v > 20:  # tiny interest expense: the ratio says nothing
        return ui.esc("> 20x")
    if v is not None and v < -20:
        return checked()
    return ui.unverified(ui.fmt_x(v, 1), conf, min_conf)


def rate_html(v: float | None) -> str:
    if v is not None and not 0 <= v <= 0.15:
        return checked()
    return ui.fmt_pct(v, 1)


def ltv_html(v: float | None, conf: float | None) -> str:
    if v is not None and not 0 <= v <= 1.5:
        return checked()
    c = 0.0 if v is not None and v > 1 else conf
    return ui.unverified(ui.fmt_pct(v), c, min_conf)


def nd_html(nd: float | None, e: float | None) -> str:
    if nd is None or not e:
        return "–"
    v = nd / e
    if v > 50:
        return ui.esc("> 50x")
    if v < 0:
        return checked()
    return ui.fmt_x(v, 1)


rows = []
for i, r in enumerate(f.to_dicts(), start=1):
    sub = " · ".join(
        x for x in (TIER[r["tier"]], SEGMENT.get(r["main_segment"] or ""), r["city"]) if x
    )
    if r.get("group"):
        sub += f" · part of {r['group']}"
    rows.append(
        {
            "rank": i,
            "score": f"{r['score']:.0f}" if r["score"] is not None else "–",
            "name": f'<a href="issuer?org={r["org_nr"]}" target="_self">{ui.esc(r["name"])}</a>'
            f'<span class="sub">{ui.esc(sub)}</span>',
            "ltv": ltv_html(r["ltv"], r["conf_ltv"])
            + (ui.basis_tag(r["value_basis"], short=True) if show_basis else ""),
            "icr": icr_html(r["icr"], r["conf_icr"]),
            "nd_ebitda": nd_html(r["net_debt"], r["ebitda"]),
            "rate": rate_html(r["avg_rate"]),
            "due12": ratio(r["debt_due_12m"], r["gross_debt"], ui.fmt_pct),
            "pv": ui.fmt_num(r["property_value"]),
            "next": r["next_maturity"],
        }
    )
ui.table(
    rows,
    [
        ui.Col("rank", "", "dim"),
        ui.Col("score", "Score", "big"),
        ui.Col("name", "Company", "name", raw_html=True),
        ui.Col("ltv", "LTV", "num", raw_html=True),
        ui.Col("icr", "ICR", "num", raw_html=True),
        ui.Col("nd_ebitda", "Net debt / EBITDA", "num", raw_html=True),
        ui.Col("rate", "Avg. rate", "num", raw_html=True),
        ui.Col("due12", "Debt due 12m", "num"),
        ui.Col("pv", "Property value, SEK m", "num"),
        ui.Col("next", "Next bond", "date", ui.fmt_date),
    ],
)
ui.source_line(
    f"{f.height} companies. LTV is net debt over property value; ICR is EBIT over net interest, "
    "as reported. Hover a flag for what it means. "
    f"{d.source_label('financials')}."
)

# --------------------------------------------------------------------------- export

export = f.select(
    "org_nr",
    "name",
    "tier",
    "accounting",
    "county",
    "city",
    "main_segment",
    "score",
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
).with_columns(pl.lit(str(d.ref)).alias("as_of"))

st.write("")
b1, b2, _ = st.columns([1.2, 1.2, 8])
b1.download_button(
    "Export CSV",
    export.write_csv().encode("utf-8"),
    file_name=f"headroom_companies_{d.ref}.csv",
    mime="text/csv",
)
buf = io.BytesIO()
export.write_excel(buf, worksheet="Companies", autofit=True)
b2.download_button(
    "Export Excel",
    buf.getvalue(),
    file_name=f"headroom_companies_{d.ref}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
