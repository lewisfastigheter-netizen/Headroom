"""Row schemas for the store.

Every table that holds figures carries provenance: where the number came from
(`source_url`, `page`), when it was true (`as_of` / `period_end`) and, for
extracted values, how sure the extractor was (`confidence`).
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

ORG_NR_RE = re.compile(r"^\d{6}-\d{4}$")
DEMO_ORG_RE = re.compile(r"^DEMO-\d{3}$")


class Tier(StrEnum):
    LISTED = "listed"  # listed equity (may also have bonds)
    BOND = "bond"  # unlisted equity, listed bonds
    PRIVATE = "private"  # annual reports and insolvency notices only


class Segment(StrEnum):
    RESIDENTIAL = "residential"
    LIGHT_INDUSTRIAL = "light_industrial"
    LOGISTICS = "logistics"
    OFFICE = "office"
    RETAIL = "retail"
    COMMUNITY = "community"  # social infrastructure: schools, care
    HOTEL = "hotel"
    OTHER = "other"


class EventType(StrEnum):
    WRITTEN_PROCEDURE = "written_procedure"
    WAIVER = "waiver"
    INTEREST_DEFERRAL = "interest_deferral"
    RECONSTRUCTION = "reconstruction"
    BANKRUPTCY_IN_GROUP = "bankruptcy_in_group"
    LIQUIDATION = "liquidation"
    GOING_CONCERN = "going_concern"
    LATE_ANNUAL_REPORT = "late_annual_report"
    DISPOSAL_BELOW_BOOK = "disposal_below_book"
    DISPOSAL = "disposal"
    RATING_DOWNGRADE = "rating_downgrade"
    REFINANCING_COMPLETED = "refinancing_completed"
    INTERIM_REPORT = "interim_report"
    EQUITY_RAISE = "equity_raise"


class CovenantTest(StrEnum):
    ICR = "icr"
    LTV = "ltv"
    EQUITY_RATIO = "equity_ratio"
    MIN_LIQUIDITY = "min_liquidity"


class CovenantKind(StrEnum):
    MAINTENANCE = "maintenance"
    INCURRENCE = "incurrence"


def normalise_org_nr(raw: str) -> str:
    """Return a Swedish organisationsnummer as NNNNNN-NNNN.

    Accepts 10 digits, 12 digits with a 16 century prefix, or with a hyphen.
    """
    if DEMO_ORG_RE.match(raw):
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("16"):
        digits = digits[2:]
    if len(digits) != 10:
        raise ValueError(f"not an organisationsnummer: {raw!r}")
    return f"{digits[:6]}-{digits[6:]}"


def luhn_ok(org_nr: str) -> bool:
    """Swedish org numbers end in a Luhn check digit."""
    digits = [int(c) for c in org_nr if c.isdigit()]
    total = 0
    for i, d in enumerate(digits[:-1]):
        v = d * (2 if i % 2 == 0 else 1)
        total += v - 9 if v > 9 else v
    return (10 - total % 10) % 10 == digits[-1]


class Company(BaseModel):
    org_nr: str
    lei: str | None = None
    name: str
    tier: Tier
    listed_ticker: str | None = None
    sni: str | None = None
    segment_mix: dict[str, float] = Field(default_factory=dict)  # share of property value
    region_mix: dict[str, float] = Field(default_factory=dict)
    size: float | None = None  # property value, SEK m
    source_url: str
    as_of: date

    @field_validator("org_nr")
    @classmethod
    def _org(cls, v: str) -> str:
        return normalise_org_nr(v)


class Bond(BaseModel):
    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9A-Z]$")
    org_nr: str
    nominal: float  # outstanding nominal, in `currency`, millions
    currency: str
    issue_date: date | None = None
    maturity: date
    coupon_type: str  # fixed | floating
    benchmark: str | None = None  # e.g. STIBOR 3M
    margin_bp: float | None = None
    coupon_pct: float | None = None  # fixed coupon
    secured: bool | None = None
    venue: str | None = None
    trustee: str | None = None
    source_url: str
    as_of: date


class Covenant(BaseModel):
    isin: str
    test: CovenantTest
    threshold: float
    kind: CovenantKind
    source_url: str
    page: int | None = None
    confidence: float = 1.0


class Financials(BaseModel):
    org_nr: str
    period_end: date
    period_type: str = "Q"  # Q = interim (LTM where relevant), FY = annual report
    property_value: float | None = None  # SEK m
    gross_debt: float | None = None
    net_debt: float | None = None
    ltv: float | None = None  # share, 0.55 = 55%
    icr: float | None = None  # multiple
    ebitda: float | None = None  # LTM, SEK m
    interest_expense: float | None = None  # LTM net interest, SEK m
    avg_rate: float | None = None  # share
    fixed_share: float | None = None  # share of debt fixed or hedged
    fixed_period_years: float | None = None
    equity_ratio: float | None = None
    cash: float | None = None
    undrawn_facilities: float | None = None
    debt_due_12m: float | None = None
    debt_due_24m: float | None = None  # cumulative, includes the 12m bucket
    source_url: str
    page: int | None = None
    confidence: float = 1.0


class Event(BaseModel):
    org_nr: str
    date: date
    type: EventType
    severity: int  # 1 low, 2 medium, 3 high
    title: str
    source_url: str
    source_name: str | None = None


class Price(BaseModel):
    ticker: str
    date: date
    close: float
    nav_per_share: float | None = None  # latest reported NAV (EPRA NRV/NTA) at that date
    source_url: str


class Rate(BaseModel):
    series: str  # policy_rate, swestr, tbill_3m
    date: date
    value: float  # percent
    source_url: str
