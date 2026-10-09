"""KPI extraction from interim and annual reports.

The LLM reads the selected pages and returns every figure it finds with the
page it came from, a short verbatim quote and a confidence value. It is told to
copy figures, not compute them. The only arithmetic happens here, in code, and
each derived figure is labelled as derived:

* EBITDA (LTM) = reported ICR x annualised net interest, when not reported.
* Debt due within 12 and 24 months, from the reported maturity table, assuming
  maturities fall evenly within each calendar-year bucket.
* Net debt = interest-bearing debt - cash, when not reported.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from headroom.extract.documents import Document, render_for_llm, select_pages
from headroom.extract.llm import structured
from headroom.model import valuation

PROMPT_VERSION = "kpi-v2"

FieldName = Literal[
    "property_value",
    "interest_bearing_debt",
    "net_debt",
    "cash",
    "undrawn_facilities",
    "ltv_pct",
    "icr",
    "net_interest_expense",
    "avg_interest_rate_pct",
    "fixed_or_hedged_share_pct",
    "avg_fixed_rate_period_years",
    "avg_debt_maturity_years",
    "equity_ratio_pct",
    "nav_per_share",
]


class Figure(BaseModel):
    field: FieldName
    value: float = Field(description="Number as printed, converted to millions for amounts")
    page: int = Field(description="Page number from the <page number=...> tag")
    confidence: float = Field(description="0 to 1. Below 0.8 if the figure is ambiguous")
    evidence: str = Field(description="Short verbatim quote containing the figure, max 160 chars")


class MaturityBucket(BaseModel):
    kind: Literal["calendar_year", "months"]
    year: int = Field(description="Calendar year for kind=calendar_year, else 0")
    months_from: int = Field(description="For kind=months: start in months from period end, else 0")
    months_to: int = Field(description="For kind=months: end in months from period end, else 0")
    amount: float = Field(description="Total interest-bearing debt maturing, millions")
    page: int


class Share(BaseModel):
    name: str
    share_pct: float
    page: int


class ReportKPIs(BaseModel):
    is_financial_report: bool = Field(description="False if this is not an interim/annual report")
    company_name: str
    period_end: str = Field(description="Balance sheet date, YYYY-MM-DD")
    currency: str = Field(description="Reporting currency, ISO code such as SEK or EUR")
    interest_period_months: int = Field(
        description="Months covered by net_interest_expense: 3, 6, 9 or 12 (12 if rolling)"
    )
    figures: list[Figure]
    debt_maturities: list[MaturityBucket]
    segments: list[Share] = Field(description="Share of property value by property type")
    regions: list[Share] = Field(description="Share of property value by city or region")


SYSTEM = """You extract financial figures from Nordic listed and bond-issuing property companies' reports.

Rules:
- Copy figures exactly as reported for the most recent balance-sheet date. Do not calculate,
  estimate or carry over prior-period figures. If a figure is not reported, leave it out.
- Amounts in millions of the reporting currency (convert bn to millions; thousands to millions).
- Percentages as percentage numbers: 47.5 means 47.5 %.
- net_interest_expense: interest expense net of interest income on interest-bearing debt, as a
  positive number (use net financial items excluding derivative value changes if that is what is
  reported). Prefer a rolling 12-month figure and
  set interest_period_months to 12; otherwise give the report period's figure and its length.
- icr: interest coverage ratio as reported (prefer rolling 12 months).
- fixed_or_hedged_share_pct: share of interest-bearing debt with fixed rate or hedged by derivatives.
- nav_per_share: EPRA NRV (or long-term net asset value) per share, in currency units, not millions.
- debt_maturities: the maturity (capital tie-up) table for total interest-bearing debt. Use
  kind=calendar_year for years such as 2027, kind=months for buckets such as "< 1 year".
- segments and regions: shares of property value (or rental value if that is all that is given).
- page: the number in the <page number="..."> tag where the figure appears.
- confidence: your confidence that value, unit and period are right. Use < 0.8 when the label is
  ambiguous, the figure is pro forma, or units are unclear.
"""


def extract_report(doc: Document, company: str) -> tuple[ReportKPIs, dict, list[int]]:
    pages = select_pages(doc)
    user = (
        f"Company: {company}\nSource: {doc.url}\n\n"
        f"Report pages (selected):\n\n{render_for_llm(doc, pages)}"
    )
    parsed, meta = structured(
        ReportKPIs, SYSTEM, user, key=f"{PROMPT_VERSION}|{doc.sha256}|{','.join(map(str, pages))}"
    )
    return parsed, meta, pages


# --------------------------------------------------------------------------- mapping

SEGMENT_RULES = [
    ("residential", r"resident|bostad|bostäder|hyresrätt|apartment|student"),
    ("logistics", r"logist|warehouse|lager"),
    ("light_industrial", r"industr|light industrial|lätt industri"),
    (
        "community",
        r"community|samhäll|public|offentlig|education|skol|care|vård|äldreboende|health",
    ),
    ("office", r"office|kontor"),
    ("retail", r"retail|handel|butik|shopping|grocery|dagligvaru"),
    ("hotel", r"hotel|hotell"),
]


def map_segment(name: str) -> str:
    for seg, rx in SEGMENT_RULES:
        if re.search(rx, name, re.IGNORECASE):
            return seg
    return "other"


def _mix(shares: list[Share], mapper=lambda s: s) -> dict[str, float]:
    total = sum(max(s.share_pct, 0) for s in shares)
    if total <= 0:
        return {}
    out: dict[str, float] = {}
    for s in shares:
        k = mapper(s.name.strip())
        out[k] = out.get(k, 0) + max(s.share_pct, 0) / total
    return {k: round(v, 4) for k, v in out.items()}


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.44


def debt_due(buckets: list[MaturityBucket], period_end: date, horizon_months: int) -> float | None:
    """Debt maturing within `horizon_months` of the period end."""
    if not buckets:
        return None
    total = 0.0
    for b in buckets:
        if b.kind == "months":
            lo, hi = b.months_from, max(b.months_to, b.months_from)
            if hi <= lo:
                continue
            overlap = max(0.0, min(hi, horizon_months) - lo)
            total += b.amount * overlap / (hi - lo)
        else:
            if b.year <= 0:
                continue
            start = date(b.year, 1, 1)
            end = date(b.year, 12, 31)
            lo = max(_months_between(period_end, start), 0.0)
            hi = _months_between(period_end, end)
            if hi <= 0:
                total += b.amount  # bucket for the current year: all remaining within horizon
                continue
            span = hi - lo if hi > lo else 1.0
            overlap = max(0.0, min(hi, horizon_months) - lo)
            total += b.amount * overlap / span
    return round(total, 1)


def to_rows(
    k: ReportKPIs,
    org_nr: str,
    url: str,
    fx: float,
    min_conf: float = 0.8,
    period_type: str = "Q",
) -> tuple[dict, list[dict], dict]:
    """Map an extraction to a financials row, provenance rows and company enrichment.

    `fx` converts the reporting currency to SEK.
    """
    pe = date.fromisoformat(k.period_end)
    best: dict[str, Figure] = {}
    for f in k.figures:
        if f.field not in best or f.confidence > best[f.field].confidence:
            best[f.field] = f

    def v(name: str, money: bool = False, pct: bool = False) -> float | None:
        f = best.get(name)
        if f is None:
            return None
        x = f.value
        if money:
            x *= fx
        if pct:
            x /= 100
        return x

    prov: list[dict] = []

    def cite(
        field: str,
        src: str,
        method: str = "llm",
        conf: float | None = None,
        page: int | None = None,
    ) -> None:
        f = best.get(src)
        prov.append(
            {
                "table_name": "financials",
                "org_nr": org_nr,
                "period_end": pe,
                "field": field,
                "source_url": url,
                "page": page if page is not None else (f.page if f else None),
                "confidence": conf if conf is not None else (f.confidence if f else None),
                "method": method,
            }
        )

    row = {
        "org_nr": org_nr,
        "period_end": pe,
        "period_type": period_type,
        "source_url": url,
        "page": None,
        # wording around the property value says whether it is market or book value
        "value_basis": valuation.classify(best["property_value"].evidence)
        if "property_value" in best
        else None,
    }
    direct = {
        "property_value": ("property_value", True, False),
        "gross_debt": ("interest_bearing_debt", True, False),
        "net_debt": ("net_debt", True, False),
        "cash": ("cash", True, False),
        "undrawn_facilities": ("undrawn_facilities", True, False),
        "ltv": ("ltv_pct", False, True),
        "icr": ("icr", False, False),
        "avg_rate": ("avg_interest_rate_pct", False, True),
        "fixed_share": ("fixed_or_hedged_share_pct", False, True),
        "fixed_period_years": ("avg_fixed_rate_period_years", False, False),
        "equity_ratio": ("equity_ratio_pct", False, True),
    }
    for col, (src, money, pct) in direct.items():
        row[col] = v(src, money, pct)
        if row[col] is not None:
            cite(col, src)

    # derived: net debt
    if row["net_debt"] is None and row["gross_debt"] is not None and row["cash"] is not None:
        row["net_debt"] = row["gross_debt"] - row["cash"]
        confs = [best[x].confidence for x in ("interest_bearing_debt", "cash")]
        cite("net_debt", "interest_bearing_debt", "derived: debt - cash", min(confs))

    # derived: LTV when only net debt and property value are reported
    if row["ltv"] is None and row["net_debt"] is not None and row["property_value"]:
        row["ltv"] = row["net_debt"] / row["property_value"]
        conf = min(p["confidence"] for p in prov if p["field"] in ("net_debt", "property_value"))
        cite("ltv", "property_value", "derived: net debt / property value", conf)

    # derived: interest, annualised when needed; else average rate x debt
    ni = v("net_interest_expense", money=True)
    if ni is not None:
        months = k.interest_period_months if k.interest_period_months in (3, 6, 9, 12) else 12
        row["interest_expense"] = abs(ni) * 12 / months
        conf = best["net_interest_expense"].confidence * (1.0 if months == 12 else 0.9)
        method = "llm" if months == 12 else f"derived: {months}-month figure annualised"
        cite("interest_expense", "net_interest_expense", method, conf)
    elif row["avg_rate"] is not None and row["gross_debt"] is not None:
        row["interest_expense"] = row["avg_rate"] * row["gross_debt"]
        conf = min(
            best["avg_interest_rate_pct"].confidence, best["interest_bearing_debt"].confidence
        )
        cite(
            "interest_expense", "avg_interest_rate_pct", "derived: average rate x debt", conf * 0.9
        )
    else:
        row["interest_expense"] = None
    if row["icr"] is not None and row["interest_expense"]:
        row["ebitda"] = row["icr"] * row["interest_expense"]
        conf = min(best["icr"].confidence, prov[-1]["confidence"])
        cite("ebitda", "icr", "derived: ICR x net interest", conf)
    else:
        row["ebitda"] = None

    # derived: maturities
    due12 = debt_due(k.debt_maturities, pe, 12)
    due24 = debt_due(k.debt_maturities, pe, 24)
    row["debt_due_12m"] = due12 * fx if due12 is not None else None
    row["debt_due_24m"] = due24 * fx if due24 is not None else None
    if k.debt_maturities:
        page = k.debt_maturities[0].page
        for col in ("debt_due_12m", "debt_due_24m"):
            cite(col, "", "derived: maturity table, even within year", 0.85, page)

    confs = [p["confidence"] for p in prov if p["confidence"] is not None]
    row["confidence"] = min(confs) if confs else None

    nav = best.get("nav_per_share")
    enrich = {
        "segment_mix": json.dumps(_mix(k.segments, map_segment), ensure_ascii=False)
        if k.segments
        else None,
        "region_mix": json.dumps(_mix(k.regions), ensure_ascii=False) if k.regions else None,
        "size": row["property_value"],
        "nav_per_share": nav.value * fx if nav else None,
        "nav_page": nav.page if nav else None,
        "currency": k.currency,
    }
    return row, prov, enrich


def quarter_end(d: date) -> date:
    m = 3 * ((d.month - 1) // 3) + 3
    return date(d.year, m, calendar.monthrange(d.year, m)[1])
