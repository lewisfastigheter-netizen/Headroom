"""Companies: every property company in the universe, ranked by seller score."""

from __future__ import annotations

import io
import json

import polars as pl
import streamlit as st

from headroom.model import geo, valuation
from ui import components as ui
from ui.data import get_data
from ui.labels import SEGMENT, TIER

d = get_data()
bands = d.cfg["motivated_seller"]["bands"]
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

n_high = df.filter(pl.col("score") >= bands["high"]).height
ui.hero(
    "Companies",
    f"{n_high} of {df.height} companies show the balance sheet of a motivated seller.",
    "Ranked on a 0–100 seller score. Method on the Method page.",
)

# --------------------------------------------------------------------------- filters

ui.section(None, "Ranking")
counties = (
    pl.read_csv(geo.GEO / "kommuner.csv", schema_overrides={"kommun_code": pl.Utf8})
    .sort("county_code")["county"]
    .unique(maintain_order=True)
    .to_list()
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
# Segments come from listed companies' and bond issuers' reports; private annual reports
# have no segment split.
show_seg = not type_sel or any(t in ("listed", "bond") for t in type_sel)
acc_sel: list[str] = (state.get("f_acc") or []) if show_acc else []
seg_sel: list[str] = (state.get("f_seg") or []) if show_seg else []
county = state.get("f_county", "All counties")
city = state.get(f"city_{county}", "All cities")


def apply(frame: pl.DataFrame, skip: str = "") -> pl.DataFrame:
    """Filter by every current choice except `skip` (used to count each option)."""
    if type_sel and skip != "type":
        frame = frame.filter(pl.col("tier").is_in(type_sel))
    if acc_sel and skip != "acc":
        frame = frame.filter(pl.col("accounting").is_in(acc_sel))
    if seg_sel and skip != "seg":
        frame = frame.filter(pl.col("main_segment").is_in(seg_sel))
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


segments = list(SEGMENT)
filters = ["type", *(["acc"] if show_acc else []), *(["seg"] if show_seg else []), "county", "city"]
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
if show_seg:
    cols["seg"].multiselect(
        "Segment",
        segments,
        format_func=lambda s: SEGMENT.get(s, s),
        placeholder="All segments",
        key="f_seg",
    )
    grey_empty("f_seg", segments, present(apply(df, "seg"), "main_segment"))
cols["county"].selectbox("County", ["All counties", *counties], key="f_county")
grey_empty("f_county", counties, present(apply(df, "county"), "county"), offset=1)
city_options = sorted(present(df.filter(pl.col("county") == county), "city"))
cols["city"].selectbox(
    "City", ["All cities", *city_options], disabled=county == "All counties", key=f"city_{county}"
)
grey_empty(
    f"city_{county}".replace(" ", "-"), city_options, present(apply(df, "city"), "city"), 1
)
if styles:
    ui.render("<style>" + "\n".join(styles) + "</style>")

f = apply(df)
nb = (
    d.t["bond"]
    .filter((pl.col("maturity") > d.ref) & (pl.col("maturity").dt.year() < 2100))  # skip perpetuals
    .group_by("org_nr")
    .agg(pl.col("maturity").min().alias("next_maturity"))
)
f = f.join(nb, on="org_nr", how="left").sort("score", descending=True, nulls_last=True)

# --------------------------------------------------------------------------- table

# With an accounting filter every row has the same basis, so the BOOK flag adds nothing.
show_basis = not acc_sel


def ratio(a: float | None, b: float | None, fmt) -> str:
    return fmt(a / b) if a is not None and b else "–"


rows = []
for i, r in enumerate(f.to_dicts(), start=1):
    sub = " · ".join(
        x for x in (TIER[r["tier"]], SEGMENT.get(r["main_segment"] or ""), r["city"]) if x
    )
    ltv_conf = 0.0 if r["ltv"] is not None and not 0 <= r["ltv"] <= 1 else r["conf_ltv"]
    rows.append(
        {
            "rank": i,
            "score": f"{r['score']:.0f}" if r["score"] is not None else "–",
            "name": f'<a href="issuer?org={r["org_nr"]}" target="_self">{ui.esc(r["name"])}</a>'
            f'<span class="sub">{ui.esc(sub)}</span>',
            "ltv": ui.unverified(ui.fmt_pct(r["ltv"]), ltv_conf, min_conf)
            + (ui.basis_tag(r["value_basis"], short=True) if show_basis else ""),
            "icr": ui.unverified(ui.fmt_x(r["icr"], 1), r["conf_icr"], min_conf),
            "nd_ebitda": ratio(r["net_debt"], r["ebitda"], lambda v: ui.fmt_x(v, 1)),
            "rate": ui.fmt_pct(r["avg_rate"], 1),
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
        ui.Col("nd_ebitda", "Net debt / EBITDA", "num"),
        ui.Col("rate", "Avg. rate", "num"),
        ui.Col("due12", "Debt due 12m", "num"),
        ui.Col("pv", "Property value, SEK m", "num"),
        ui.Col("next", "Next bond", "date", ui.fmt_date),
    ],
    bleed=True,
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
