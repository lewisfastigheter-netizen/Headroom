"""Fictional demo dataset.

Every company, bond, figure and event produced here is invented. Names use
Greek-letter placeholders, org numbers use the reserved pattern DEMO-NNN and
ISINs start with SEDEMO, so nothing can be mistaken for, or joined to, a real
entity. Source URLs use the `demo://` scheme.

The data is deterministic (fixed seed) and designed to exercise every code
path: healthy and distressed issuers, all three tiers, missing components,
covenant breaches under rate shock, and low-confidence extracted fields.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import polars as pl

from headroom.store.db import write_snapshot

REFERENCE_DATE = date(2026, 9, 30)
SEED = 20261008
TRUSTEE = "Demo Trustee AB"

QUARTERS = [
    date(2024, 9, 30),
    date(2024, 12, 31),
    date(2025, 3, 31),
    date(2025, 6, 30),
    date(2025, 9, 30),
    date(2025, 12, 31),
    date(2026, 3, 31),
    date(2026, 6, 30),
]
YEARS = [date(2023, 12, 31), date(2024, 12, 31), date(2025, 12, 31)]


@dataclass
class BondSpec:
    maturity: date
    nominal: float
    floating: bool = True
    margin_bp: float = 500
    coupon_pct: float | None = None
    secured: bool = False
    issued: date = date(2023, 6, 15)
    icr_cov: float | None = 1.5
    ltv_cov: float | None = 0.70
    eq_cov: float | None = None


@dataclass
class Co:
    n: int
    name: str
    tier: str
    seg: dict[str, float]
    reg: dict[str, float]
    pv: float  # property value, SEK m, latest
    ltv: tuple[float, float]  # start, end
    icr: tuple[float, float]
    avg_rate: tuple[float, float]
    fixed_share: float
    cash_pct: float  # cash as share of property value
    undrawn_pct: float
    bank_due_12m_pct: float  # bank debt due in 12m as share of gross debt
    bank_due_24m_pct: float
    ticker: str | None = None
    pnav_end: float | None = None
    sni: str = "68.201"
    bonds: list[BondSpec] = field(default_factory=list)
    lei: bool = True


def _org(n: int) -> str:
    return f"DEMO-{n:03d}"


COMPANIES: list[Co] = [
    # ---------------- listed ----------------
    Co(
        1,
        "Alfa Bostäder AB (publ)",
        "listed",
        {"residential": 0.88, "community": 0.07, "retail": 0.05},
        {"Stockholm": 0.55, "Uppsala": 0.30, "Gävle": 0.15},
        28_400,
        (0.47, 0.48),
        (2.6, 2.4),
        (0.031, 0.029),
        0.72,
        0.012,
        0.06,
        0.08,
        0.15,
        ticker="ALFA-B.DEMO",
        pnav_end=0.84,
        bonds=[
            BondSpec(date(2027, 9, 14), 1000, margin_bp=165, icr_cov=1.75, ltv_cov=0.65),
            BondSpec(
                date(2029, 3, 2), 800, floating=False, coupon_pct=4.1, icr_cov=1.75, ltv_cov=0.65
            ),
        ],
    ),
    Co(
        2,
        "Beta Logistikfastigheter AB (publ)",
        "listed",
        {"logistics": 0.92, "light_industrial": 0.08},
        {"Gothenburg": 0.45, "Jönköping": 0.30, "Borås": 0.25},
        18_200,
        (0.50, 0.52),
        (2.3, 2.1),
        (0.034, 0.033),
        0.65,
        0.015,
        0.05,
        0.07,
        0.16,
        ticker="BETA.DEMO",
        pnav_end=0.94,
        bonds=[
            BondSpec(date(2027, 4, 20), 700, margin_bp=210, icr_cov=1.5, ltv_cov=0.65),
            BondSpec(date(2028, 11, 5), 900, margin_bp=240, icr_cov=1.5, ltv_cov=0.65),
        ],
    ),
    Co(
        3,
        "Gamma Fastigheter AB (publ)",
        "listed",
        {"office": 0.48, "residential": 0.32, "retail": 0.20},
        {"Stockholm": 0.70, "Malmö": 0.18, "Sundsvall": 0.12},
        45_600,
        (0.55, 0.59),
        (1.9, 1.55),
        (0.033, 0.038),
        0.48,
        0.010,
        0.03,
        0.14,
        0.26,
        ticker="GAMM-B.DEMO",
        pnav_end=0.54,
        bonds=[
            BondSpec(date(2026, 12, 8), 1500, margin_bp=290, icr_cov=1.5, ltv_cov=0.65),
            BondSpec(date(2027, 6, 1), 1250, margin_bp=325, icr_cov=1.5, ltv_cov=0.65),
            BondSpec(
                date(2028, 2, 15), 1000, floating=False, coupon_pct=5.25, icr_cov=1.5, ltv_cov=0.65
            ),
        ],
    ),
    Co(
        4,
        "Delta Samhällsfastigheter AB (publ)",
        "listed",
        {"community": 0.81, "residential": 0.19},
        {"Stockholm": 0.35, "Gothenburg": 0.20, "Luleå": 0.25, "Umeå": 0.20},
        30_100,
        (0.58, 0.63),
        (1.7, 1.35),
        (0.030, 0.036),
        0.55,
        0.008,
        0.02,
        0.16,
        0.30,
        ticker="DELT.DEMO",
        pnav_end=0.46,
        bonds=[
            BondSpec(
                date(2027, 1, 25), 1200, margin_bp=360, icr_cov=1.5, ltv_cov=0.70, eq_cov=0.25
            ),
            BondSpec(
                date(2027, 10, 12), 1400, margin_bp=410, icr_cov=1.5, ltv_cov=0.70, eq_cov=0.25
            ),
        ],
    ),
    Co(
        5,
        "Epsilon Industri AB (publ)",
        "listed",
        {"light_industrial": 0.78, "logistics": 0.22},
        {"Malmö": 0.45, "Helsingborg": 0.30, "Lund": 0.25},
        9_500,
        (0.45, 0.45),
        (3.1, 3.0),
        (0.036, 0.034),
        0.70,
        0.020,
        0.07,
        0.05,
        0.11,
        ticker="EPSI.DEMO",
        pnav_end=1.04,
        bonds=[BondSpec(date(2028, 5, 30), 600, margin_bp=230, icr_cov=1.75, ltv_cov=0.65)],
    ),
    Co(
        6,
        "Zeta Kontor AB (publ)",
        "listed",
        {"office": 0.90, "retail": 0.10},
        {"Stockholm": 0.85, "Gothenburg": 0.15},
        22_300,
        (0.56, 0.60),
        (2.0, 1.65),
        (0.032, 0.035),
        0.60,
        0.011,
        0.04,
        0.12,
        0.22,
        ticker="ZETA.DEMO",
        pnav_end=0.61,
        bonds=[BondSpec(date(2027, 3, 18), 900, margin_bp=275, icr_cov=1.5, ltv_cov=0.65)],
    ),
    Co(
        7,
        "Eta Hyresbostäder AB (publ)",
        "listed",
        {"residential": 0.95, "retail": 0.05},
        {"Gothenburg": 0.50, "Borås": 0.30, "Trollhättan": 0.20},
        6_500,
        (0.53, 0.56),
        (2.1, 1.85),
        (0.033, 0.035),
        0.58,
        0.012,
        0.03,
        0.10,
        0.20,
        ticker="ETA.DEMO",
        pnav_end=0.69,
        bonds=[BondSpec(date(2027, 8, 24), 500, margin_bp=330, icr_cov=1.5, ltv_cov=0.65)],
    ),
    Co(
        8,
        "Theta Handel AB (publ)",
        "listed",
        {"retail": 0.84, "light_industrial": 0.16},
        {"Västerås": 0.30, "Örebro": 0.30, "Karlstad": 0.40},
        4_800,
        (0.50, 0.50),
        (2.7, 2.6),
        (0.035, 0.034),
        0.66,
        0.018,
        0.05,
        0.06,
        0.12,
        ticker="THET.DEMO",
        pnav_end=0.79,
    ),
    # ---------------- bond issuers, unlisted equity ----------------
    Co(
        9,
        "Iota Fastigheter Holding AB (publ)",
        "bond",
        {"residential": 0.74, "retail": 0.26},
        {"Stockholm": 0.60, "Södertälje": 0.40},
        3_150,
        (0.62, 0.72),
        (1.45, 1.05),
        (0.055, 0.071),
        0.10,
        0.006,
        0.0,
        0.05,
        0.08,
        bonds=[
            BondSpec(
                date(2026, 10, 30),
                900,
                margin_bp=650,
                secured=True,
                icr_cov=1.25,
                ltv_cov=0.75,
                issued=date(2023, 10, 30),
            )
        ],
    ),
    Co(
        10,
        "Kappa Lager & Logistik AB (publ)",
        "bond",
        {"logistics": 0.85, "light_industrial": 0.15},
        {"Örebro": 0.55, "Västerås": 0.45},
        2_400,
        (0.60, 0.65),
        (1.8, 1.45),
        (0.052, 0.058),
        0.25,
        0.010,
        0.0,
        0.08,
        0.15,
        bonds=[
            BondSpec(
                date(2027, 5, 14), 600, margin_bp=575, secured=True, icr_cov=1.25, ltv_cov=0.70
            )
        ],
    ),
    Co(
        11,
        "Lambda Bostad Utveckling AB (publ)",
        "bond",
        {"residential": 1.0},
        {"Stockholm": 0.40, "Uppsala": 0.60},
        1_800,
        (0.61, 0.70),
        (1.2, 0.85),
        (0.068, 0.083),
        0.0,
        0.004,
        0.0,
        0.10,
        0.18,
        bonds=[
            BondSpec(
                date(2027, 3, 3),
                500,
                margin_bp=850,
                secured=True,
                icr_cov=None,
                ltv_cov=0.75,
                eq_cov=0.20,
            )
        ],
    ),
    Co(
        12,
        "My Industrifastigheter AB (publ)",
        "bond",
        {"light_industrial": 0.70, "logistics": 0.30},
        {"Linköping": 0.55, "Norrköping": 0.45},
        1_500,
        (0.57, 0.58),
        (1.9, 1.8),
        (0.054, 0.055),
        0.35,
        0.014,
        0.01,
        0.05,
        0.10,
        bonds=[
            BondSpec(date(2027, 9, 21), 400, margin_bp=600, secured=True, icr_cov=1.5, ltv_cov=0.70)
        ],
    ),
    Co(
        13,
        "Ny Samhällsbyggnad AB (publ)",
        "bond",
        {"community": 0.90, "office": 0.10},
        {"Sundsvall": 0.40, "Östersund": 0.30, "Umeå": 0.30},
        5_200,
        (0.60, 0.66),
        (1.6, 1.28),
        (0.041, 0.049),
        0.40,
        0.007,
        0.01,
        0.12,
        0.24,
        bonds=[
            BondSpec(date(2027, 2, 11), 800, margin_bp=480, icr_cov=1.5, ltv_cov=0.70, eq_cov=0.25),
            BondSpec(date(2028, 6, 9), 700, margin_bp=520, icr_cov=1.5, ltv_cov=0.70, eq_cov=0.25),
        ],
    ),
    Co(
        14,
        "Xi Hotellfastigheter AB (publ)",
        "bond",
        {"hotel": 0.92, "retail": 0.08},
        {"Stockholm": 0.35, "Gothenburg": 0.35, "Åre": 0.30},
        2_900,
        (0.60, 0.62),
        (1.7, 1.6),
        (0.050, 0.052),
        0.45,
        0.012,
        0.02,
        0.06,
        0.14,
        bonds=[
            BondSpec(
                date(2028, 1, 19), 650, margin_bp=550, secured=True, icr_cov=1.25, ltv_cov=0.70
            )
        ],
    ),
    Co(
        15,
        "Omikron Bostäder AB (publ)",
        "bond",
        {"residential": 0.93, "community": 0.07},
        {"Uppsala": 0.70, "Enköping": 0.30},
        2_100,
        (0.57, 0.55),
        (1.8, 2.0),
        (0.051, 0.047),
        0.55,
        0.020,
        0.03,
        0.04,
        0.09,
        bonds=[
            BondSpec(
                date(2029, 4, 26),
                500,
                margin_bp=450,
                secured=True,
                icr_cov=1.5,
                ltv_cov=0.70,
                issued=date(2026, 4, 26),
            )
        ],
    ),
    Co(
        16,
        "Pi Logistik Norden AB (publ)",
        "bond",
        {"logistics": 1.0},
        {"Stockholm": 0.40, "Helsinki": 0.35, "Oslo": 0.25},
        7_800,
        (0.49, 0.50),
        (2.5, 2.4),
        (0.042, 0.041),
        0.62,
        0.016,
        0.05,
        0.05,
        0.12,
        bonds=[BondSpec(date(2028, 9, 7), 1000, margin_bp=325, icr_cov=1.75, ltv_cov=0.65)],
    ),
    Co(
        17,
        "Rho Fastighets AB (publ)",
        "bond",
        {"office": 0.60, "retail": 0.40},
        {"Eskilstuna": 0.50, "Nyköping": 0.50},
        1_200,
        (0.66, 0.74),
        (1.25, 0.95),
        (0.061, 0.074),
        0.05,
        0.003,
        0.0,
        0.12,
        0.20,
        bonds=[
            BondSpec(
                date(2027, 2, 26), 350, margin_bp=750, secured=True, icr_cov=1.25, ltv_cov=0.75
            )
        ],
    ),
    Co(
        18,
        "Sigma Kommersiella Fastigheter AB (publ)",
        "bond",
        {"office": 0.35, "light_industrial": 0.35, "retail": 0.30},
        {"Gothenburg": 0.50, "Halmstad": 0.50},
        3_900,
        (0.58, 0.61),
        (1.7, 1.42),
        (0.046, 0.052),
        0.30,
        0.009,
        0.01,
        0.09,
        0.17,
        bonds=[BondSpec(date(2027, 11, 30), 750, margin_bp=525, icr_cov=1.25, ltv_cov=0.70)],
    ),
    # ---------------- private ABs ----------------
    Co(
        19,
        "Tau Fastigheter i Västerås AB",
        "private",
        {"light_industrial": 0.80, "office": 0.20},
        {"Västerås": 1.0},
        650,
        (0.62, 0.66),
        (1.6, 1.3),
        (0.048, 0.055),
        0.20,
        0.010,
        0.0,
        0.20,
        0.35,
        lei=False,
    ),
    Co(
        20,
        "Ypsilon Bostäder i Gävle AB",
        "private",
        {"residential": 1.0},
        {"Gävle": 1.0},
        420,
        (0.66, 0.73),
        (1.3, 0.9),
        (0.050, 0.062),
        0.10,
        0.004,
        0.0,
        0.25,
        0.40,
        lei=False,
    ),
    Co(
        21,
        "Fi Lagerhus AB",
        "private",
        {"logistics": 1.0},
        {"Jönköping": 1.0},
        900,
        (0.52, 0.53),
        (2.2, 2.1),
        (0.045, 0.046),
        0.40,
        0.015,
        0.0,
        0.08,
        0.15,
        lei=False,
    ),
    Co(
        22,
        "Chi Industribyggnader AB",
        "private",
        {"light_industrial": 1.0},
        {"Linköping": 1.0},
        300,
        (0.55, 0.60),
        (1.9, 1.5),
        (0.047, 0.052),
        0.20,
        0.020,
        0.0,
        0.10,
        0.20,
        sni="25.110",
        lei=False,
    ),
    Co(
        23,
        "Psi Hyresfastigheter AB",
        "private",
        {"residential": 0.90, "retail": 0.10},
        {"Örebro": 1.0},
        1_100,
        (0.63, 0.71),
        (1.4, 1.0),
        (0.049, 0.060),
        0.15,
        0.005,
        0.0,
        0.22,
        0.38,
        lei=False,
    ),
    Co(
        24,
        "Omega Förvaltning AB",
        "private",
        {"residential": 0.50, "office": 0.50},
        {"Karlstad": 1.0},
        520,
        (0.45, 0.44),
        (2.8, 2.9),
        (0.042, 0.041),
        0.50,
        0.030,
        0.0,
        0.05,
        0.10,
        lei=False,
    ),
]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _isin(n: int) -> str:
    return f"SEDEMO{n:05d}X"


def _demo_url(kind: str, *parts: object) -> str:
    return "demo://" + "/".join([kind, *[str(p) for p in parts]])


def build() -> dict[str, pl.DataFrame]:
    rng = random.Random(SEED)
    companies, bonds, covenants, fins, prov, events, prices = [], [], [], [], [], [], []
    bond_no = 0

    for c in COMPANIES:
        org = _org(c.n)
        companies.append(
            {
                "org_nr": org,
                "lei": f"DEMO00LEI{c.n:03d}00000000"[:20] if c.lei else None,
                "name": c.name,
                "tier": c.tier,
                "listed_ticker": c.ticker,
                "sni": c.sni,
                "segment_mix": json.dumps(c.seg, ensure_ascii=False),
                "region_mix": json.dumps(c.reg, ensure_ascii=False),
                "size": float(c.pv),
                "universe_rule": "demo",
                "source_url": _demo_url("company", org),
                "as_of": REFERENCE_DATE,
            }
        )

        isins: list[tuple[str, BondSpec]] = []
        for b in c.bonds:
            bond_no += 1
            isin = _isin(bond_no)
            isins.append((isin, b))
            bonds.append(
                {
                    "isin": isin,
                    "org_nr": org,
                    "full_name": f"{c.name.split(' AB')[0]} "
                    + ("FRN" if b.floating else f"{b.coupon_pct}%")
                    + f" {b.maturity.year}",
                    "nominal": float(b.nominal),
                    "currency": "SEK",
                    "nominal_sek": float(b.nominal),
                    "issue_date": b.issued,
                    "maturity": b.maturity,
                    "coupon_type": "floating" if b.floating else "fixed",
                    "benchmark": "STIBOR 3M" if b.floating else None,
                    "margin_bp": float(b.margin_bp) if b.floating else None,
                    "coupon_pct": b.coupon_pct,
                    "secured": b.secured,
                    "venue": "Demo Exchange",
                    "trustee": TRUSTEE,
                    "source_url": _demo_url("firds", isin),
                    "as_of": REFERENCE_DATE,
                }
            )
            terms = _demo_url("terms", isin)
            for test, thr in (("icr", b.icr_cov), ("ltv", b.ltv_cov), ("equity_ratio", b.eq_cov)):
                if thr is None:
                    continue
                covenants.append(
                    {
                        "isin": isin,
                        "test": test,
                        "threshold": thr,
                        "kind": "maintenance",
                        "source_url": terms,
                        "page": rng.randint(18, 41),
                        "confidence": round(rng.uniform(0.86, 0.98), 2),
                    }
                )
            # incurrence test, typically tighter on ICR
            if b.icr_cov is not None:
                covenants.append(
                    {
                        "isin": isin,
                        "test": "icr",
                        "threshold": round(b.icr_cov + 0.25, 2),
                        "kind": "incurrence",
                        "source_url": terms,
                        "page": rng.randint(18, 41),
                        "confidence": round(rng.uniform(0.86, 0.98), 2),
                    }
                )

        periods = YEARS if c.tier == "private" else QUARTERS
        if c.n == 19:  # Tau: FY2025 annual report not filed (late)
            periods = YEARS[:-1]
        for i, pe in enumerate(periods):
            t = i / max(len(periods) - 1, 1)
            if c.n == 19:
                t = i / (len(YEARS) - 1)
            pv = c.pv * (1 + 0.012 * (len(periods) - 1 - i) * (1 if c.ltv[1] <= c.ltv[0] else -1))
            pv = round(pv * rng.uniform(0.995, 1.005), 0)
            ltv = round(_lerp(*c.ltv, t) + rng.uniform(-0.004, 0.004), 3)
            cash = round(pv * c.cash_pct * rng.uniform(0.85, 1.15), 0)
            net_debt = round(ltv * pv, 0)
            gross_debt = net_debt + cash
            avg_rate = round(_lerp(*c.avg_rate, t), 4)
            interest = round(gross_debt * avg_rate, 0)
            icr = round(_lerp(*c.icr, t) + rng.uniform(-0.03, 0.03), 2)
            ebitda = round(icr * interest, 0)
            equity_ratio = round(max(0.05, 1 - gross_debt / (pv + cash) - 0.09), 3)
            bond_due_12 = sum(b.nominal for _, b in isins if pe < b.maturity <= pe + timedelta(365))
            bond_due_24 = sum(b.nominal for _, b in isins if pe < b.maturity <= pe + timedelta(730))
            bank = gross_debt - sum(b.nominal for _, b in isins)
            due12 = round(bond_due_12 + max(bank, 0) * c.bank_due_12m_pct, 0)
            due24 = round(bond_due_24 + max(bank, 0) * c.bank_due_24m_pct, 0)
            kind = "annual-report" if c.tier == "private" else "interim-report"
            url = _demo_url(kind, org, pe.isoformat())
            row = {
                "org_nr": org,
                "period_end": pe,
                "period_type": "FY" if c.tier == "private" else "Q",
                "property_value": pv,
                "gross_debt": gross_debt,
                "net_debt": net_debt,
                "ltv": ltv,
                "icr": icr,
                "ebitda": ebitda,
                "interest_expense": interest,
                "avg_rate": avg_rate if c.tier != "private" else None,
                "fixed_share": c.fixed_share if c.tier != "private" else None,
                "fixed_period_years": round(rng.uniform(1.2, 3.4), 1)
                if c.tier != "private"
                else None,
                "equity_ratio": equity_ratio,
                "cash": cash,
                "undrawn_facilities": round(pv * c.undrawn_pct, 0) if c.tier != "private" else None,
                "debt_due_12m": due12 if c.tier != "private" else None,
                "debt_due_24m": due24 if c.tier != "private" else None,
                "source_url": url,
                "page": None,
                "confidence": 1.0,
                "value_basis": "book_value" if c.tier == "private" else "fair_value",
            }
            # Private ABs: annual reports disclose short-term debt only.
            if c.tier == "private":
                row["debt_due_12m"] = round(max(bank, 0) * c.bank_due_12m_pct, 0)
            # field-level provenance
            min_conf = 1.0
            for fld in (
                "property_value",
                "net_debt",
                "ltv",
                "icr",
                "ebitda",
                "interest_expense",
                "avg_rate",
                "fixed_share",
                "equity_ratio",
                "cash",
                "undrawn_facilities",
                "debt_due_12m",
                "debt_due_24m",
            ):
                if row[fld] is None:
                    continue
                conf = round(rng.uniform(0.88, 0.99), 2)
                # two deliberately weak extractions so the unverified state is visible
                if (c.n, fld, i) in {
                    (13, "fixed_share", len(periods) - 1),
                    (18, "undrawn_facilities", len(periods) - 1),
                }:
                    conf = 0.62
                min_conf = min(min_conf, conf)
                prov.append(
                    {
                        "table_name": "financials",
                        "org_nr": org,
                        "period_end": pe,
                        "field": fld,
                        "source_url": url,
                        "page": rng.randint(3, 28),
                        "confidence": conf,
                        "method": "demo",
                    }
                )
            row["confidence"] = min_conf
            fins.append(row)

        # prices: weekly closes, two years, ending at the target P/NAV
        if c.ticker:
            last = [f for f in fins if f["org_nr"] == org][-1]
            shares = 100.0  # demo: NAV per share scaled to a share count of 100m
            nav_ps = round((last["property_value"] - last["net_debt"]) * 0.92 / shares, 2)
            end_px = nav_ps * c.pnav_end
            n_weeks = 104
            px = [end_px]
            for _ in range(n_weeks):
                px.append(
                    px[-1] / math.exp(rng.gauss(-0.002 if c.pnav_end < 0.7 else 0.0005, 0.035))
                )
            px = list(reversed(px))
            start = REFERENCE_DATE - timedelta(weeks=n_weeks)
            for w, p in enumerate(px):
                d = start + timedelta(weeks=w)
                prices.append(
                    {
                        "ticker": c.ticker,
                        "date": d,
                        "close": round(p, 2),
                        "nav_per_share": nav_ps,
                        "source_url": _demo_url("prices", c.ticker),
                    }
                )

        # routine interim reports
        if c.tier != "private":
            for pe in QUARTERS[-4:]:
                events.append(
                    {
                        "org_nr": org,
                        "date": pe + timedelta(days=rng.randint(38, 52)),
                        "type": "interim_report",
                        "severity": 1,
                        "title": f"Interim report, period ending {pe:%-d %B %Y}",
                        "source_url": _demo_url("press", org, f"interim-{pe.isoformat()}"),
                        "source_name": "Demo newswire",
                    }
                )

    E = events.append

    def ev(n: int, d: date, typ: str, sev: int, title: str, src: str = "Demo newswire") -> None:
        E(
            {
                "org_nr": _org(n),
                "date": d,
                "type": typ,
                "severity": sev,
                "title": title,
                "source_url": _demo_url("press", _org(n), d.isoformat(), typ),
                "source_name": src,
            }
        )

    ev(
        9,
        date(2026, 5, 29),
        "going_concern",
        3,
        "Annual report 2025: auditor draws attention to material uncertainty on going concern",
    )
    ev(
        9,
        date(2026, 7, 14),
        "written_procedure",
        3,
        "Written procedure initiated on senior secured bonds: maturity extension from October 2026 "
        "to April 2028 and consent to an orderly sale of all group properties",
        "Demo trustee notice",
    )
    ev(
        9,
        date(2026, 8, 11),
        "written_procedure",
        3,
        "Written procedure: quorum reached, bondholders approve extension and wind-down mandate",
        "Demo trustee notice",
    )
    ev(
        9,
        date(2026, 9, 3),
        "disposal_below_book",
        2,
        "Sale of two residential properties in Södertälje at 9 per cent below book value",
    )
    ev(
        11,
        date(2026, 4, 2),
        "interest_deferral",
        3,
        "Deferral of quarterly interest payment on senior secured bonds; standstill agreed with "
        "a majority of bondholders",
    )
    ev(
        11,
        date(2026, 6, 26),
        "written_procedure",
        3,
        "Written procedure to amend terms: PIK interest for four quarters and waiver of "
        "equity ratio test",
        "Demo trustee notice",
    )
    ev(
        11,
        date(2026, 9, 15),
        "reconstruction",
        3,
        "Subsidiary Lambda Projekt 4 AB applies for company reconstruction",
        "Demo gazette",
    )
    ev(
        17,
        date(2026, 6, 4),
        "going_concern",
        3,
        "Annual report 2025: auditor's report includes going-concern remark",
    )
    ev(
        17,
        date(2026, 8, 20),
        "waiver",
        2,
        "Bondholders grant waiver of the maintenance LTV test for the Q2 2026 test date",
        "Demo trustee notice",
    )
    ev(
        13,
        date(2026, 3, 12),
        "waiver",
        2,
        "Lenders waive ICR covenant on secured bank facilities for 2026 test dates",
    )
    ev(
        13,
        date(2025, 11, 5),
        "rating_downgrade",
        2,
        "Issuer rating lowered one notch, outlook negative",
        "Demo rating agency",
    )
    ev(
        4,
        date(2026, 2, 19),
        "waiver",
        2,
        "Waiver obtained on ICR covenant under bank facilities; dividend suspended",
    )
    ev(
        4,
        date(2026, 5, 7),
        "rating_downgrade",
        2,
        "Rating lowered to sub-investment grade, outlook stable",
        "Demo rating agency",
    )
    ev(
        4,
        date(2026, 8, 28),
        "disposal_below_book",
        2,
        "Divestment of 14 school and care properties at 12 per cent below book value",
    )
    ev(
        3,
        date(2026, 6, 18),
        "rating_downgrade",
        2,
        "Outlook revised to negative on refinancing needs",
        "Demo rating agency",
    )
    ev(3, date(2026, 9, 10), "disposal", 1, "Sale of office property in Malmö at book value")
    ev(
        6,
        date(2026, 7, 2),
        "rating_downgrade",
        2,
        "Rating lowered one notch on weaker interest coverage",
        "Demo rating agency",
    )
    ev(
        10,
        date(2026, 9, 22),
        "disposal",
        1,
        "Mandate given to advisers to explore sale of two logistics properties in Örebro",
    )
    ev(
        15,
        date(2026, 4, 28),
        "refinancing_completed",
        1,
        "New SEK 500m senior secured bond issued; 2026 maturity repurchased in full",
    )
    ev(
        19,
        date(2026, 7, 1),
        "late_annual_report",
        2,
        "Annual report for financial year 2025 not filed within seven months of year end",
        "Demo company register",
    )
    ev(
        20,
        date(2026, 5, 20),
        "bankruptcy_in_group",
        3,
        "Bankruptcy order against subsidiary Ypsilon Bostäder Norr AB",
        "Demo gazette",
    )
    ev(
        23,
        date(2026, 8, 6),
        "reconstruction",
        3,
        "Application for company reconstruction approved by district court",
        "Demo gazette",
    )
    ev(
        18,
        date(2026, 5, 15),
        "disposal_below_book",
        2,
        "Sale of retail park in Halmstad at 7 per cent below book value",
    )

    # rates: illustrative only (labelled as fictional in demo mode)
    rates = []
    d = date(2023, 10, 1)
    i = 0
    while d <= REFERENCE_DATE:
        policy = 4.0 if i < 8 else max(1.75, 4.0 - 0.25 * (i - 7))
        policy = round(policy if i < 30 else 2.0, 2)
        stibor = round(policy + 0.12 + rng.uniform(-0.05, 0.05), 2)
        rates.append(
            {
                "series": "policy_rate",
                "date": d,
                "value": policy,
                "source_url": "demo://rates/policy_rate",
            }
        )
        rates.append(
            {
                "series": "tbill_3m",
                "date": d,
                "value": stibor,
                "source_url": "demo://rates/tbill_3m",
            }
        )
        i += 1
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)

    return {
        "company": pl.DataFrame(companies),
        "bond": pl.DataFrame(bonds),
        "covenant": pl.DataFrame(covenants),
        "financials": pl.DataFrame(fins),
        "provenance": pl.DataFrame(prov),
        "event": pl.DataFrame(events),
        "price": pl.DataFrame(prices),
        "rate": pl.DataFrame(rates),
    }


def write() -> None:
    tables = build()
    ref = REFERENCE_DATE.isoformat()
    write_snapshot(
        "demo",
        tables,
        fictional=True,
        as_of={name: ref for name in tables},
        notes="Fictional demo dataset. Companies, bonds, figures and events are invented.",
    )


if __name__ == "__main__":
    write()
