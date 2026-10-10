"""Underwriting: market evidence by segment and place, and a hold-period cash flow.

The cash flow is the back-of-envelope model an acquisitions analyst runs before a full
model exists: year-1 NOI grown at a flat rate, interest-only debt at a fixed all-in
rate, exit at NOI of the year after the hold divided by an exit yield. Everything is
annual and in SEK m. The same model is written to Excel with live formulas
(`to_excel`), so the figures on screen and in the workbook agree.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Any

import yaml

from headroom.config import CONFIG_DIR

MARKET_FILE = CONFIG_DIR / "underwriting" / "market.yaml"

SEGMENTS = {
    "residential": "Residential",
    "logistics": "Logistics",
    "light_industrial": "Light industrial",
    "community": "Community service",
    "hotel": "Hotel",
    "office": "Office",
    "retail": "Retail",
}

MARKETS = {
    "stockholm": "Stockholm",
    "gothenburg": "Gothenburg",
    "malmo": "Malmö",
    "oresund": "Öresund",
    "regional": "Regional cities",
    "sweden": "Sweden",
}

# SCB's metropolitan areas (storstadsområden), as on the location snapshot.
STORSTAD = {"0010": "stockholm", "0020": "gothenburg", "0030": "malmo"}


@lru_cache(maxsize=1)
def market_data() -> dict[str, Any]:
    with open(MARKET_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def market_of(kommun_code: str | None, segment: str, storstad: str | None = None) -> str:
    """The broker market a municipality falls in, for a segment: Greater Stockholm,
    Greater Gothenburg and Greater Malmö by SCB's metropolitan areas; for logistics and
    light industrial, all of Skåne is the Öresund market; everything else is regional."""
    metro = STORSTAD.get(storstad or "")
    if metro is None and kommun_code and kommun_code[:2] == "01":
        metro = "stockholm"
    if segment in ("logistics", "light_industrial") and (
        metro == "malmo" or (kommun_code or "")[:2] == "12"
    ):
        return "oresund"
    return metro or "regional"


def source(key: str) -> dict:
    return market_data()["sources"].get(key, {})


def rows(kind: str, segment: str | None = None, market: str | None = None) -> list[dict]:
    """Rows of a list in market.yaml, filtered on segment and market when given."""
    out = []
    for r in market_data().get(kind) or []:
        if segment and r.get("segment") != segment:
            continue
        if market and r.get("market") != market:
            continue
        out.append({**r, "_source": source(r["source"])})
    return out


def pick(kind: str, segment: str, market: str) -> dict | None:
    """The row for this segment and market; failing that, the national row; failing
    that, the regional one. None when the source has nothing for the segment."""
    for m in (market, "sweden", "regional"):
        hit = rows(kind, segment, m)
        if hit:
            return hit[0]
    return None


# --------------------------------------------------------------------------- cash flow


@dataclass(frozen=True)
class Assumptions:
    price: float  # SEK m, purchase price of the property (or the property value of the company)
    noi: float  # SEK m, net operating income in year 1
    rent_growth: float = 0.02  # NOI growth per year
    ltv: float = 0.55  # debt over price at entry
    interest: float = 0.045  # all-in cost of debt per year
    exit_yield: float = 0.05
    hold: int = 5  # years
    entry_cost: float = 0.015  # transaction costs on purchase, share of price
    exit_cost: float = 0.01  # transaction costs on sale, share of exit value
    capex: float = 0.0  # SEK m per year, not capitalised into NOI

    @property
    def entry_yield(self) -> float:
        return self.noi / self.price if self.price else 0.0


@dataclass
class Result:
    years: list[int]
    noi: list[float]
    interest: list[float]
    capex: list[float]
    cf_unlevered: list[float]
    cf_levered: list[float]
    debt: float
    equity: float
    exit_value: float
    irr_unlevered: float | None
    irr_levered: float | None
    moic: float | None
    icr_y1: float | None
    debt_yield: float | None
    cash_yield_y1: float | None
    profit: float


def irr(cfs: Sequence[float], lo: float = -0.99, hi: float = 10.0) -> float | None:
    """Internal rate of return by bisection; None when the flows do not change sign."""
    if not cfs or min(cfs) >= 0 or max(cfs) <= 0:
        return None

    def npv(r: float) -> float:
        return sum(c / (1 + r) ** t for t, c in enumerate(cfs))

    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def run(a: Assumptions) -> Result:
    years = list(range(0, a.hold + 1))
    noi = [0.0] + [a.noi * (1 + a.rent_growth) ** (t - 1) for t in years[1:]]
    debt = a.price * a.ltv
    equity = a.price * (1 + a.entry_cost) - debt
    interest = [0.0] + [debt * a.interest for _ in years[1:]]
    capex = [0.0] + [a.capex for _ in years[1:]]
    exit_noi = a.noi * (1 + a.rent_growth) ** a.hold
    exit_value = exit_noi / a.exit_yield if a.exit_yield else 0.0
    net_exit = exit_value * (1 - a.exit_cost)
    cf_u = [-a.price * (1 + a.entry_cost)]
    cf_l = [-equity]
    for t in years[1:]:
        u = noi[t] - capex[t]
        l_ = u - interest[t]
        if t == a.hold:
            u += net_exit
            l_ += net_exit - debt
        cf_u.append(u)
        cf_l.append(l_)
    paid_in = equity
    moic = sum(c for c in cf_l[1:]) / paid_in if paid_in > 0 else None
    return Result(
        years=years,
        noi=noi,
        interest=interest,
        capex=capex,
        cf_unlevered=cf_u,
        cf_levered=cf_l,
        debt=debt,
        equity=equity,
        exit_value=exit_value,
        irr_unlevered=irr(cf_u),
        irr_levered=irr(cf_l),
        moic=moic,
        icr_y1=noi[1] / interest[1] if len(noi) > 1 and interest[1] else None,
        debt_yield=noi[1] / debt if debt else None,
        cash_yield_y1=(noi[1] - capex[1] - interest[1]) / equity if equity > 0 else None,
        profit=sum(cf_l),
    )


def solve_price(a: Assumptions, target_irr: float, levered: bool = True) -> float | None:
    """The purchase price at which the IRR equals the target, all else equal."""

    def gap(price: float) -> float:
        r = run(replace(a, price=price))
        got = r.irr_levered if levered else r.irr_unlevered
        return (got if got is not None else -1.0) - target_irr

    lo, hi = a.noi / 0.30, a.noi / 0.005  # entry yields of 30% down to 0.5%
    if gap(lo) < 0 or gap(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if gap(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def sensitivity(
    a: Assumptions,
    exit_yields: Sequence[float],
    growths: Sequence[float],
    metric: Callable[[Result], float | None] = lambda r: r.irr_levered,
) -> list[list[float | None]]:
    """Grid of the metric: one row per exit yield, one column per NOI growth rate."""
    return [
        [metric(run(replace(a, exit_yield=ey, rent_growth=g))) for g in growths]
        for ey in exit_yields
    ]


# --------------------------------------------------------------------------- Excel


def to_excel(a: Assumptions, title: str, notes: Sequence[tuple[str, str]] = ()) -> bytes:
    """Workbook with the inputs and a cash flow written as formulas, so the model can be
    changed and audited in Excel. `notes` are (input, source) pairs shown beside it."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Model")
    bold = wb.add_format({"bold": True})
    head = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#00839B"})
    inp = wb.add_format({"font_color": "#0000FF", "num_format": "#,##0.00"})
    pct_in = wb.add_format({"font_color": "#0000FF", "num_format": "0.00%"})
    num = wb.add_format({"num_format": "#,##0.0"})
    pct = wb.add_format({"num_format": "0.0%"})
    mult = wb.add_format({"num_format": "0.00x"})
    dim = wb.add_format({"font_color": "#6A6A6A", "italic": True})

    ws.set_column(0, 0, 34)
    ws.set_column(1, 1, 14)
    ws.set_column(2, 2 + a.hold, 12)
    ws.set_column(3 + a.hold, 3 + a.hold, 60)
    ws.write(0, 0, title, bold)
    ws.write(1, 0, "SEK m unless stated. Blue cells are inputs.", dim)

    inputs = [
        ("Purchase price", "price", a.price, inp),
        ("NOI, year 1", "noi", a.noi, inp),
        ("NOI growth per year", "growth", a.rent_growth, pct_in),
        ("Loan-to-value at entry", "ltv", a.ltv, pct_in),
        ("All-in interest rate", "rate", a.interest, pct_in),
        ("Exit yield", "exit_yield", a.exit_yield, pct_in),
        ("Hold period, years", "hold", a.hold, inp),
        ("Transaction costs, purchase", "entry_cost", a.entry_cost, pct_in),
        ("Transaction costs, sale", "exit_cost", a.exit_cost, pct_in),
        ("Capex per year", "capex", a.capex, inp),
    ]
    ws.write(3, 0, "Inputs", head)
    ws.write(3, 1, "", head)
    ref: dict[str, str] = {}
    note_of = dict(notes)
    for i, (label, key, value, fmt) in enumerate(inputs):
        row = 4 + i
        ws.write(row, 0, label)
        ws.write(row, 1, value, fmt)
        ref[key] = f"$B${row + 1}"
        if label in note_of:
            ws.write(row, 2, note_of[label], dim)

    r0 = 4 + len(inputs) + 1
    ws.write(r0, 0, "Derived", head)
    ws.write(r0, 1, "", head)
    derived = [
        ("Entry yield", f"={ref['noi']}/{ref['price']}", pct),
        ("Debt", f"={ref['price']}*{ref['ltv']}", num),
        ("Equity", f"={ref['price']}*(1+{ref['entry_cost']})-{ref['price']}*{ref['ltv']}", num),
        (
            "Exit value",
            f"={ref['noi']}*(1+{ref['growth']})^{ref['hold']}/{ref['exit_yield']}",
            num,
        ),
    ]
    for i, (label, formula, fmt) in enumerate(derived):
        ws.write(r0 + 1 + i, 0, label)
        ws.write_formula(r0 + 1 + i, 1, formula, fmt)
        ref[label] = f"$B${r0 + 2 + i}"

    c0 = r0 + len(derived) + 2
    ws.write(c0, 0, "Cash flow", head)
    for t in range(a.hold + 1):
        ws.write(c0, 1 + t, f"Year {t}", head)
    labels = ["NOI", "Capex", "Interest", "Sale, net of costs", "Debt repaid", "Unlevered CF",
              "Levered CF"]  # fmt: skip
    for i, label in enumerate(labels):
        ws.write(c0 + 1 + i, 0, label)
    col = xlsxwriter.utility.xl_col_to_name
    for t in range(a.hold + 1):
        c = col(1 + t)
        r = c0 + 2  # Excel row of NOI (1-based)
        if t == 0:
            ws.write_number(c0 + 1, 1, 0, num)
            ws.write_number(c0 + 2, 1, 0, num)
            ws.write_number(c0 + 3, 1, 0, num)
            ws.write_number(c0 + 4, 1, 0, num)
            ws.write_number(c0 + 5, 1, 0, num)
            ws.write_formula(c0 + 6, 1, f"=-{ref['price']}*(1+{ref['entry_cost']})", num)
            ws.write_formula(c0 + 7, 1, f"=-{ref['Equity']}", num)
            continue
        last = f"IF({t}={ref['hold']},1,0)"
        ws.write_formula(c0 + 1, 1 + t, f"={ref['noi']}*(1+{ref['growth']})^({t}-1)", num)
        ws.write_formula(c0 + 2, 1 + t, f"=-{ref['capex']}", num)
        ws.write_formula(c0 + 3, 1 + t, f"=-{ref['Debt']}*{ref['rate']}", num)
        ws.write_formula(c0 + 4, 1 + t, f"={last}*{ref['Exit value']}*(1-{ref['exit_cost']})", num)
        ws.write_formula(c0 + 5, 1 + t, f"=-{last}*{ref['Debt']}", num)
        ws.write_formula(c0 + 6, 1 + t, f"={c}{r}+{c}{r + 1}+{c}{r + 3}", num)
        ws.write_formula(c0 + 7, 1 + t, f"={c}{r}+{c}{r + 1}+{c}{r + 2}+{c}{r + 3}+{c}{r + 4}", num)

    last_col = col(1 + a.hold)
    k0 = c0 + 9
    ws.write(k0, 0, "Returns", head)
    ws.write(k0, 1, "", head)
    u_row, l_row = c0 + 7, c0 + 8  # 1-based rows of the unlevered and levered CF
    ws.write(k0 + 1, 0, "Unlevered IRR")
    ws.write_formula(k0 + 1, 1, f"=IRR(B{u_row}:{last_col}{u_row})", pct)
    ws.write(k0 + 2, 0, "Levered IRR")
    ws.write_formula(k0 + 2, 1, f"=IRR(B{l_row}:{last_col}{l_row})", pct)
    ws.write(k0 + 3, 0, "Equity multiple")
    ws.write_formula(k0 + 3, 1, f"=SUM(C{l_row}:{last_col}{l_row})/{ref['Equity']}", mult)
    ws.write(k0 + 4, 0, "Interest cover, year 1")
    ws.write_formula(k0 + 4, 1, f"=C{c0 + 2}/-C{c0 + 4}", mult)
    ws.write(k0 + 5, 0, "Debt yield, year 1")
    ws.write_formula(k0 + 5, 1, f"=C{c0 + 2}/{ref['Debt']}", pct)

    ws.write(k0 + 7, 0, "Interest is paid only; the loan is repaid at exit. Exit value is NOI "
             "in the year after the hold divided by the exit yield.", dim)  # fmt: skip

    src = wb.add_worksheet("Inputs and sources")
    src.set_column(0, 0, 34)
    src.set_column(1, 1, 100)
    src.write(0, 0, "Input", head)
    src.write(0, 1, "Source", head)
    for i, (k, v) in enumerate(notes):
        src.write(1 + i, 0, k)
        src.write(1 + i, 1, v)
    wb.close()
    return buf.getvalue()


def as_dict(a: Assumptions) -> dict:
    return asdict(a)
