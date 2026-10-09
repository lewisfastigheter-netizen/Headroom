"""Motivated-seller score, strategy-fit score and opportunity label.

Design rules
------------
* Every component returns its inputs, its 0-100 sub-score and a note, so the UI
  can show exactly how a score was built.
* A component without data is marked unavailable. Its weight is redistributed
  pro rata across the available components and the company is flagged. Nothing
  is imputed. Where a component runs on partial inputs (for example cash
  without undrawn facilities), the note says so.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import polars as pl

from headroom.config import weights as load_weights
from headroom.model.stress import shocked_icr

COMPONENT_LABELS = {
    "refinancing": "Refinancing pressure",
    "covenant": "Covenant headroom",
    "leverage": "Leverage and coverage",
    "events": "Events",
    "market": "Market",
}
FIT_LABELS = {"segment": "Segment exposure", "region": "Growth regions", "size": "Deal size"}


def ramp(x: float, zero: float, full: float) -> float:
    """Linear 0-100 between `zero` and `full`, clipped. Works in either direction."""
    if full == zero:
        return 100.0 if x >= full else 0.0
    t = (x - zero) / (full - zero)
    return 100.0 * min(max(t, 0.0), 1.0)


@dataclass
class Component:
    key: str
    label: str
    weight: float
    available: bool
    score: float | None = None
    effective_weight: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def contribution(self) -> float:
        return (self.score or 0.0) * self.effective_weight


@dataclass
class CovenantRow:
    isin: str
    test: str
    kind: str
    threshold: float
    actual: float | None
    actual_stressed: float | None
    headroom: float | None
    headroom_stressed: float | None
    source_url: str
    page: int | None
    confidence: float


@dataclass
class ScoreCard:
    org_nr: str
    score: float | None
    components: list[Component]
    flags: list[str]
    covenants: list[CovenantRow]
    fit: float | None
    fit_components: list[Component]
    opportunity: str
    opportunity_rationale: str
    coverage: float  # share of nominal weight with data

    def component(self, key: str) -> Component:
        return next(c for c in self.components if c.key == key)


def _reweight(components: list[Component]) -> tuple[float | None, float]:
    avail = [c for c in components if c.available and c.score is not None]
    total = sum(c.weight for c in avail)
    nominal = sum(c.weight for c in components)
    for c in components:
        c.effective_weight = c.weight / total if (c.available and total) else 0.0
    if not avail:
        return None, 0.0
    return sum(c.contribution for c in avail), total / nominal


def _headroom(test: str, actual: float | None, threshold: float) -> float | None:
    if actual is None or threshold == 0:
        return None
    if test == "ltv":  # maximum test
        return (threshold - actual) / threshold
    return (actual - threshold) / threshold  # minimum tests


# --------------------------------------------------------------------------- components


def refinancing(
    fin: dict | None, cfg: dict, bond_due: tuple[float, float] | None = None
) -> Component:
    """`bond_due` = (bonds due within 12m, within 24m) from FIRDS, used when the
    reported maturity profile is missing. Bank loans are then not included."""
    c = Component("refinancing", COMPONENT_LABELS["refinancing"], 0.0, False)
    notes = []
    if fin and fin.get("debt_due_12m") is None and bond_due and fin.get("cash") is not None:
        fin = {**fin, "debt_due_12m": bond_due[0], "debt_due_24m": bond_due[1]}
        notes.append("bond maturities from FIRDS only; bank loans not reported")
    if not fin or fin.get("debt_due_12m") is None or fin.get("cash") is None:
        c.note = "Debt maturity profile or cash not reported"
        return c
    due12 = fin["debt_due_12m"]
    due24 = fin.get("debt_due_24m")
    if due24 is None:
        need = due12
        notes.append("12-month maturities only; 13-24 month bucket not reported")
    else:
        need = due12 + cfg["weight_12_24m"] * max(due24 - due12, 0.0)
    undrawn = fin.get("undrawn_facilities")
    if undrawn is None:
        liquidity = fin["cash"]
        notes.append("undrawn facilities not reported; liquidity is cash only")
    else:
        liquidity = fin["cash"] + undrawn
    ratio = need / liquidity if liquidity > 0 else math.inf
    c.available = True
    if cfg.get("scale") == "log":
        r = max(min(ratio, 1e6), 1e-6)
        c.score = ramp(math.log(r), math.log(cfg["ratio_zero"]), math.log(cfg["ratio_full"]))
    else:
        c.score = ramp(min(ratio, 1e6), cfg["ratio_zero"], cfg["ratio_full"])
    c.inputs = {
        "due_12m": due12,
        "due_24m": due24,
        "need": need,
        "cash": fin["cash"],
        "undrawn": undrawn,
        "liquidity": liquidity,
        "ratio": ratio,
    }
    joined = "; ".join(notes)
    c.note = joined[:1].upper() + joined[1:]
    return c


def covenant(fin: dict | None, covs: list[dict], cfg: dict) -> tuple[Component, list[CovenantRow]]:
    c = Component("covenant", COMPONENT_LABELS["covenant"], 0.0, False)
    rows: list[CovenantRow] = []
    if not covs:
        c.note = "No bond covenants on file"
        return c, rows
    if not fin:
        c.note = "No financials to test covenants against"
        return c, rows
    bp = cfg["stress_bp"]
    shock = shocked_icr(
        fin.get("ebitda"),
        fin.get("interest_expense"),
        fin.get("gross_debt"),
        fin.get("fixed_share"),
        bp,
    )
    for cv in covs:
        test = cv["test"]
        if test == "icr":
            actual, stressed = fin.get("icr"), shock.icr
        elif test == "ltv":
            actual = stressed = fin.get("ltv")
        elif test == "equity_ratio":
            actual = stressed = fin.get("equity_ratio")
        elif test == "min_liquidity":
            actual = stressed = fin.get("cash")
        else:
            actual = stressed = None
        rows.append(
            CovenantRow(
                isin=cv["isin"],
                test=test,
                kind=cv["kind"],
                threshold=cv["threshold"],
                actual=actual,
                actual_stressed=stressed,
                headroom=_headroom(test, actual, cv["threshold"]),
                headroom_stressed=_headroom(test, stressed, cv["threshold"]),
                source_url=cv["source_url"],
                page=cv.get("page"),
                confidence=cv.get("confidence", 1.0),
            )
        )
    maint = [r for r in rows if r.kind == "maintenance"]
    basis = maint or rows
    tested = [r for r in basis if r.headroom is not None]
    if not tested:
        c.note = "Covenant tests found, but the tested metrics are not reported"
        return c, rows
    base_row = min(tested, key=lambda r: r.headroom)
    stress_row = min(
        tested, key=lambda r: r.headroom_stressed if r.headroom_stressed is not None else r.headroom
    )
    base = base_row.headroom
    stressed = (
        stress_row.headroom_stressed
        if stress_row.headroom_stressed is not None
        else stress_row.headroom
    )
    sw = cfg.get("stress_weight", 0.5)
    s_base = ramp(base, cfg["headroom_zero"], cfg["headroom_full"])
    s_stress = ramp(stressed, cfg["headroom_zero"], cfg["headroom_full"])
    c.available = True
    c.score = (1 - sw) * s_base + sw * s_stress
    c.inputs = {
        "min_headroom": base,
        "min_headroom_stressed": stressed,
        "binding_test": base_row.test,
        "binding_isin": base_row.isin,
        "binding_test_stressed": stress_row.test,
        "stress_bp": bp,
        "sub_scores": {"base": s_base, "stressed": s_stress},
        "shock_assumption": shock.assumption,
    }
    if not maint:
        c.note = "Only incurrence tests on file; used as a proxy"
    return c, rows


def leverage(fin: dict | None, cfg: dict) -> Component:
    c = Component("leverage", COMPONENT_LABELS["leverage"], 0.0, False)
    if not fin:
        c.note = "No financials"
        return c
    subs: dict[str, float] = {}
    inputs: dict[str, Any] = {}
    if fin.get("ltv") is not None:
        subs["ltv"] = ramp(fin["ltv"], **cfg["ltv"])
        inputs["ltv"] = fin["ltv"]
    if fin.get("icr") is not None:
        subs["icr"] = ramp(fin["icr"], **cfg["icr"])
        inputs["icr"] = fin["icr"]
    nd, eb = fin.get("net_debt"), fin.get("ebitda")
    if nd is not None and eb:
        nde = nd / eb if eb > 0 else 99.0
        subs["net_debt_ebitda"] = ramp(nde, **cfg["net_debt_ebitda"])
        inputs["net_debt_ebitda"] = nde
    if not subs:
        c.note = "LTV, ICR and net debt/EBITDA not reported"
        return c
    c.available = True
    c.score = sum(subs.values()) / len(subs)
    c.inputs = {**inputs, "sub_scores": subs}
    missing = {"ltv", "icr", "net_debt_ebitda"} - subs.keys()
    if missing:
        c.note = "Average of available measures; missing: " + ", ".join(sorted(missing))
    return c


def events(evs: list[dict], ref: date, cfg: dict) -> Component:
    c = Component("events", COMPONENT_LABELS["events"], 0.0, True)
    half = cfg["half_life_days"]
    sev = cfg["severity"]
    total = 0.0
    contrib = []
    for e in evs:
        pts = sev.get(e["type"], 0)
        if pts == 0:
            continue
        age = (ref - e["date"]).days
        if age < 0:
            continue
        decayed = pts * 0.5 ** (age / half)
        total += decayed
        contrib.append(
            {
                "date": e["date"],
                "type": e["type"],
                "points": pts,
                "age_days": age,
                "decayed": decayed,
                "title": e["title"],
            }
        )
    c.score = min(total, cfg["cap"])
    c.inputs = {"events": sorted(contrib, key=lambda x: -x["decayed"]), "raw_total": total}
    if not contrib:
        c.note = "No scored events in the monitored sources"
    return c


def market(ticker: str | None, prices: pl.DataFrame | None, ref: date, cfg: dict) -> Component:
    c = Component("market", COMPONENT_LABELS["market"], 0.0, False)
    if not ticker:
        c.note = "Not listed"
        return c
    if prices is None or prices.is_empty():
        c.note = "No price data"
        return c
    p = prices.filter(pl.col("date") <= ref).sort("date")
    if p.is_empty():
        c.note = "No price data before the reference date"
        return c
    last = p.row(-1, named=True)
    year = p.filter(pl.col("date") >= ref - timedelta(days=365))
    peak = year["close"].max()
    subs, inputs = {}, {"close": last["close"], "close_date": last["date"]}
    if last.get("nav_per_share"):
        pnav = last["close"] / last["nav_per_share"]
        inputs.update(pnav=pnav, nav_per_share=last["nav_per_share"])
        subs["pnav_discount"] = ramp(1 - pnav, **cfg["pnav_discount"])
    if peak:
        dd = 1 - last["close"] / peak
        inputs["drawdown_52w"] = dd
        subs["drawdown_52w"] = ramp(dd, **cfg["drawdown_52w"])
    if not subs:
        c.note = "Price data incomplete"
        return c
    c.available = True
    c.score = sum(subs.values()) / len(subs)
    c.inputs = {**inputs, "sub_scores": subs}
    if "pnav_discount" not in subs:
        c.note = "NAV per share not available; drawdown only"
    return c


# --------------------------------------------------------------------------- fit


def strategy_fit(
    company: dict, fin: dict | None, cfg: dict
) -> tuple[float | None, list[Component]]:
    w = cfg["weights"]
    seg = json.loads(company.get("segment_mix") or "{}")
    reg = json.loads(company.get("region_mix") or "{}")
    comps = []
    s = Component("segment", FIT_LABELS["segment"], w["segment"], bool(seg))
    if seg:
        share = sum(v for k, v in seg.items() if k in cfg["target_segments"])
        s.score, s.inputs = 100 * share, {"target_share": share}
    else:
        s.note = "Segment split not reported"
    comps.append(s)
    r = Component("region", FIT_LABELS["region"], w["region"], bool(reg))
    if reg:
        share = sum(v for k, v in reg.items() if k in cfg["growth_regions"])
        r.score, r.inputs = 100 * share, {"growth_share": share}
    else:
        r.note = "Regional split not reported"
    comps.append(r)
    size = (fin or {}).get("property_value") or company.get("size")
    z = Component("size", FIT_LABELS["size"], w["size"], size is not None)
    if size is not None:
        b = cfg["size_sek_m"]
        if size < b["band_low"]:
            z.score = ramp(size, b["outer_low"], b["band_low"])
        elif size > b["band_high"]:
            z.score = ramp(size, b["outer_high"], b["band_high"])
        else:
            z.score = 100.0
        z.inputs = {"property_value": size}
    else:
        z.note = "Property value not reported"
    comps.append(z)
    score, _ = _reweight(comps)
    return score, comps


# --------------------------------------------------------------------------- opportunity


def opportunity(
    company: dict,
    fin: dict | None,
    card_score: float | None,
    comps: dict,
    evs: list[dict],
    fit: float | None,
    has_bonds: bool,
    cfg: dict,
) -> tuple[str, str]:
    types = {e["type"] for e in evs}
    score = card_score or 0
    size = (fin or {}).get("property_value") or company.get("size") or 0
    ltv = (fin or {}).get("ltv")
    sni = company.get("sni") or ""
    if (
        has_bonds
        and types & {"written_procedure", "interest_deferral"}
        and score >= cfg["wind_down_score"]
    ):
        return (
            "Bondholder-led wind-down",
            "Bond terms already reopened by written procedure or deferral, and the score is "
            f"above {cfg['wind_down_score']}. Assets are likely to be sold to repay bondholders.",
        )
    if types & {"reconstruction", "bankruptcy_in_group", "liquidation"}:
        label = "Portfolio sale" if size >= cfg["large_portfolio_sek_m"] else "Single-asset sale"
        return (
            label,
            "Insolvency proceedings in the group. Expect an administrator- or "
            "creditor-led sale process.",
        )
    if sni and not sni.startswith("68"):
        return (
            "Sale-leaseback",
            f"Owner-occupier outside real estate (SNI {sni}) holding its own property. "
            "A sale-leaseback releases capital without disrupting operations.",
        )
    cov = comps.get("covenant")
    if (
        ltv is not None
        and ltv >= cfg["recap_ltv"]
        and cov is not None
        and cov.available
        and (cov.score or 0) >= 60
    ):
        return (
            "Recapitalisation",
            f"LTV of {ltv:.0%} with thin covenant headroom. Equity injection or preferred "
            "capital in exchange for a stake or asset transfer.",
        )
    mkt = comps.get("market")
    if mkt is not None and mkt.available and mkt.inputs.get("pnav", 1) < 0.7 and (fit or 0) >= 50:
        return (
            "JV",
            f"Shares trade at {mkt.inputs['pnav']:.0%} of NAV. Selling a stake in a portfolio "
            "into a JV crystallises value nearer to book than an equity raise.",
        )
    if size >= cfg["large_portfolio_sek_m"]:
        return ("Portfolio sale", "Scale supports disposal of a sub-portfolio to reduce debt.")
    return (
        "Single-asset sale",
        "Small balance sheet. Individual asset sales are the likely route.",
    )


# --------------------------------------------------------------------------- driver


def reference_date(meta: dict) -> date:
    as_of = meta.get("as_of") or {}
    if meta.get("fictional") and as_of:
        return date.fromisoformat(max(as_of.values()))
    return date.today()


def score_all(
    tables: dict[str, pl.DataFrame], ref: date, events_covered: bool | None = None
) -> dict[str, ScoreCard]:
    """Score every company.

    `events_covered` says whether event sources are connected for this snapshot.
    Without them, "no events" means "not monitored", not "nothing happened", so
    the component is left unscored instead of scoring zero.
    """
    cfg = load_weights()
    if events_covered is None:
        events_covered = tables["event"].height > 0
    ms = cfg["motivated_seller"]
    companies = tables["company"].to_dicts()
    fins = tables["financials"].sort("period_end")
    latest = {r["org_nr"]: r for r in fins.filter(pl.col("period_end") <= ref).to_dicts()}
    bonds = tables["bond"]
    covs = tables["covenant"].join(bonds.select("isin", "org_nr", "maturity"), on="isin")
    covs = covs.filter(pl.col("maturity") > ref)
    events_df = tables["event"].filter(pl.col("date") <= ref)
    prices = tables["price"]
    live_b = bonds.filter(pl.col("maturity") > ref)
    nom = "nominal_sek" if "nominal_sek" in live_b.columns else "nominal"
    bond_due = {
        r["org_nr"]: (r["d12"] or 0.0, r["d24"] or 0.0)
        for r in live_b.group_by("org_nr")
        .agg(
            pl.col(nom).filter(pl.col("maturity") <= ref + timedelta(days=365)).sum().alias("d12"),
            pl.col(nom).filter(pl.col("maturity") <= ref + timedelta(days=730)).sum().alias("d24"),
        )
        .to_dicts()
    }
    out: dict[str, ScoreCard] = {}
    for co in companies:
        org = co["org_nr"]
        fin = latest.get(org)
        cv = covs.filter(pl.col("org_nr") == org).to_dicts()
        evs = events_df.filter(pl.col("org_nr") == org).to_dicts()
        px = prices.filter(pl.col("ticker") == co["listed_ticker"]) if co["listed_ticker"] else None
        comps = [
            refinancing(fin, ms["refinancing"], bond_due.get(org)),
            None,
            leverage(fin, ms["leverage"]),
            events(evs, ref, ms["events"]),
            market(co["listed_ticker"], px, ref, ms["market"]),
        ]
        cov_comp, cov_rows = covenant(fin, cv, ms["covenant"])
        comps[1] = cov_comp
        if not events_covered:
            comps[3] = Component(
                "events",
                COMPONENT_LABELS["events"],
                0.0,
                False,
                note="Event sources not connected yet",
            )
        for c in comps:
            c.weight = ms["weights"][c.key]
        if co["tier"] != "listed":
            comps[4].weight = 0.0  # market pricing does not apply to unlisted equity
        score, coverage = _reweight(comps)
        min_cov = ms.get("min_coverage", 0.5)
        insufficient = score is not None and coverage < min_cov
        if insufficient or score is None:
            score = None
        flags = [
            f"{c.label}: {c.note or 'no data'}"
            for c in comps
            if not c.available and c.key != "market"
        ]
        if co["tier"] != "listed":
            pass  # market is not expected for unlisted companies; not a data gap
        elif not comps[4].available:
            flags.append(f"Market: {comps[4].note}")
        if insufficient:
            flags.append(
                f"Not scored: data covers {coverage:.0%} of the weight, "
                f"below the {min_cov:.0%} minimum"
            )
        if fin and (fin.get("confidence") or 1) < cfg["extraction"]["min_confidence"]:
            flags.append("Some reported figures are unverified (low extraction confidence)")
        fit, fit_comps = strategy_fit(co, fin, cfg["strategy_fit"])
        has_bonds = bonds.filter((pl.col("org_nr") == org) & (pl.col("maturity") > ref)).height > 0
        if score is None:
            label, why = (
                "Not yet assessed",
                "Too little reported data to judge. Financials are needed first.",
            )
        else:
            label, why = opportunity(
                co, fin, score, {c.key: c for c in comps}, evs, fit, has_bonds, cfg["opportunity"]
            )
        out[org] = ScoreCard(
            org, score, comps, flags, cov_rows, fit, fit_comps, label, why, coverage
        )
    return out


def score_frame(cards: dict[str, ScoreCard]) -> pl.DataFrame:
    rows = []
    for org, c in cards.items():
        row = {
            "org_nr": org,
            "score": c.score,
            "fit": c.fit,
            "opportunity": c.opportunity,
            "coverage": c.coverage,
            "n_flags": len(c.flags),
        }
        for comp in c.components:
            row[f"s_{comp.key}"] = comp.score if comp.available else None
        cov = c.component("covenant")
        row["min_headroom"] = cov.inputs.get("min_headroom") if cov.available else None
        row["min_headroom_stressed"] = (
            cov.inputs.get("min_headroom_stressed") if cov.available else None
        )
        refi = c.component("refinancing")
        row["refi_ratio"] = refi.inputs.get("ratio") if refi.available else None
        rows.append(row)
    return pl.DataFrame(rows)
