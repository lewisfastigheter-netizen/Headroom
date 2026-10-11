"""Motivated-seller score, strategy-fit score and opportunity label.

How a score is built (the same for companies and for places)
------------------------------------------------------------
1. Each part turns one figure into 0-100 on a straight line: the `zero` value in
   config/weights.yaml scores 0, the `full` value scores 100, anything beyond is clipped.
2. The score is the weighted average of the parts.
3. A part without data is left out and the other parts count for more. Nothing is
   imputed. A part that does not apply (market price for an unlisted company, covenants
   for a company without bonds) is left out too, but is not a data gap.

The motivated-seller score has five parts, one rule each:

    refinancing  debt due within 12 months / (cash + undrawn facilities)
    covenant     tightest covenant headroom after a rate rise
    leverage     average of the LTV and ICR scores
    events       points for credit events in the last 12 months
    market       discount to NAV (52-week drawdown when NAV is not available)
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
    "leverage": "Leverage",
    "events": "Credit events",
    "market": "Share price",
}
FIT_LABELS = {"segment": "Segment exposure", "region": "Growth regions", "size": "Deal size"}


# --------------------------------------------------------------------------- the two rules


def ramp(x: float, zero: float, full: float, scale: str | None = None) -> float:
    """0-100 on a straight line from `zero` (0) to `full` (100), clipped.

    Works in either direction (for ICR, a lower value is worse, so zero > full).
    With scale="log" the line is drawn on log10 of the values."""
    if scale == "log":
        if x <= 0:
            return 0.0 if full > zero else 100.0
        x, zero, full = math.log10(x), math.log10(zero), math.log10(full)
    if full == zero:
        return 100.0 if x >= full else 0.0
    t = (x - zero) / (full - zero)
    return 100.0 * min(max(t, 0.0), 1.0)


def weighted_average(parts: list[tuple[float, float | None]]) -> tuple[float | None, float]:
    """(weight, score) pairs -> (score, coverage).

    Parts with no score are left out and the rest count for more. Coverage is the share
    of the total weight that had data."""
    total = sum(w for w, _ in parts)
    have = [(w, s) for w, s in parts if s is not None and w > 0]
    have_w = sum(w for w, _ in have)
    if not have_w:
        return None, 0.0
    return sum(w * s for w, s in have) / have_w, have_w / total if total else 0.0


# --------------------------------------------------------------------------- data classes


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
    applicable: bool = True  # False: the part does not apply here, so it is not a data gap

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
    coverage: float  # share of the applicable weight with data

    def component(self, key: str) -> Component:
        return next(c for c in self.components if c.key == key)


def _reweight(components: list[Component]) -> tuple[float | None, float]:
    """Weighted average of the components with data; sets each one's applied weight."""
    used = [c for c in components if c.applicable]
    score, coverage = weighted_average([(c.weight, c.score if c.available else None) for c in used])
    have_w = sum(c.weight for c in used if c.available and c.score is not None)
    for c in components:
        ok = c.applicable and c.available and c.score is not None and have_w
        c.effective_weight = c.weight / have_w if ok else 0.0
    return score, coverage


def _not_applicable(key: str, why: str) -> Component:
    return Component(key, COMPONENT_LABELS[key], 0.0, False, note=why, applicable=False)


def _headroom(test: str, actual: float | None, threshold: float) -> float | None:
    """Distance to the covenant as a share of the threshold; negative means in breach."""
    if actual is None or threshold == 0:
        return None
    if test == "ltv":  # maximum test
        return (threshold - actual) / threshold
    return (actual - threshold) / threshold  # minimum tests


# --------------------------------------------------------------------------- components


def refinancing(fin: dict | None, cfg: dict, bond_due_12m: float | None = None) -> Component:
    """Debt due within 12 months / (cash + undrawn facilities), on a log scale.

    `bond_due_12m` is bonds due within 12 months from FIRDS, used when the report has no
    maturity profile (bank loans are then missing, and the note says so)."""
    c = Component("refinancing", COMPONENT_LABELS["refinancing"], 0.0, False)
    notes = []
    due = (fin or {}).get("debt_due_12m")
    if due is None and bond_due_12m is not None:
        due = bond_due_12m
        notes.append("Bond maturities from FIRDS only; bank loans not reported")
    if not fin or due is None or fin.get("cash") is None:
        c.note = "Debt maturity profile or cash not reported"
        return c
    undrawn = fin.get("undrawn_facilities")
    if undrawn is None:
        notes.append("undrawn facilities not reported; liquidity is cash only")
    liquidity = fin["cash"] + (undrawn or 0.0)
    ratio = due / liquidity if liquidity > 0 else math.inf
    c.available = True
    c.score = ramp(min(ratio, 1e6), cfg["zero"], cfg["full"], scale="log")
    c.inputs = {
        "due_12m": due,
        "cash": fin["cash"],
        "undrawn": undrawn,
        "liquidity": liquidity,
        "ratio": ratio,
    }
    joined = "; ".join(notes)
    c.note = joined[:1].upper() + joined[1:]
    return c


def covenant(fin: dict | None, covs: list[dict], cfg: dict) -> tuple[Component, list[CovenantRow]]:
    """Tightest covenant headroom after a rate rise of `stress_bp` (only ICR moves)."""
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
    metric = {"ltv": "ltv", "equity_ratio": "equity_ratio", "min_liquidity": "cash"}
    for cv in covs:
        test = cv["test"]
        if test == "icr":
            actual, stressed = fin.get("icr"), shock.icr
        else:
            actual = fin.get(metric[test]) if test in metric else None
            stressed = actual
        base = _headroom(test, actual, cv["threshold"])
        after = _headroom(test, stressed, cv["threshold"])
        rows.append(
            CovenantRow(
                isin=cv["isin"],
                test=test,
                kind=cv["kind"],
                threshold=cv["threshold"],
                actual=actual,
                actual_stressed=stressed,
                headroom=base,
                headroom_stressed=after if after is not None else base,
                source_url=cv["source_url"],
                page=cv.get("page"),
                confidence=cv.get("confidence", 1.0),
            )
        )
    maint = [r for r in rows if r.kind == "maintenance"]
    tested = [r for r in (maint or rows) if r.headroom is not None]
    if not tested:
        c.note = "Covenant tests found, but the tested metrics are not reported"
        return c, rows
    tightest = min(tested, key=lambda r: r.headroom_stressed)
    c.available = True
    c.score = ramp(tightest.headroom_stressed, cfg["zero"], cfg["full"])
    c.inputs = {
        "min_headroom": min(r.headroom for r in tested),
        "min_headroom_stressed": tightest.headroom_stressed,
        "binding_test": tightest.test,
        "binding_isin": tightest.isin,
        "stress_bp": bp,
        "shock_assumption": shock.assumption,
    }
    if not maint:
        c.note = "Only incurrence tests on file; used as a proxy"
    return c, rows


def leverage(fin: dict | None, cfg: dict) -> Component:
    """Average of the LTV and ICR scores (one of them if the other is not reported)."""
    c = Component("leverage", COMPONENT_LABELS["leverage"], 0.0, False)
    if not fin:
        c.note = "No financials"
        return c
    subs = {k: ramp(fin[k], **cfg[k]) for k in ("ltv", "icr") if fin.get(k) is not None}
    if not subs:
        c.note = "LTV and ICR not reported"
        return c
    c.available = True
    c.score = sum(subs.values()) / len(subs)
    c.inputs = {**{k: fin[k] for k in subs}, "sub_scores": subs}
    if len(subs) == 1:
        missing = "ICR" if "ltv" in subs else "LTV"
        c.note = f"{missing} not reported; scored on the other only"
    return c


def events(evs: list[dict], ref: date, cfg: dict) -> Component:
    """Points for each credit event in the last `window_days`, added up and capped."""
    c = Component("events", COMPONENT_LABELS["events"], 0.0, True)
    points = cfg["points"]
    counted = []
    for e in evs:
        pts = points.get(e["type"], 0)
        age = (ref - e["date"]).days
        if pts and 0 <= age <= cfg["window_days"]:
            counted.append(
                {
                    "date": e["date"],
                    "type": e["type"],
                    "points": pts,
                    "age_days": age,
                    "title": e["title"],
                }
            )
    total = sum(e["points"] for e in counted)
    c.score = min(total, cfg["cap"])
    c.inputs = {
        "events": sorted(counted, key=lambda x: (-x["points"], x["age_days"])),
        "raw_total": total,
    }
    if not counted:
        c.note = "No credit events in the monitored sources in the last 12 months"
    return c


def market(prices: pl.DataFrame | None, ref: date, cfg: dict) -> Component:
    """Discount to NAV; the fall from the 52-week high when NAV is not available."""
    c = Component("market", COMPONENT_LABELS["market"], 0.0, False)
    if prices is None or prices.is_empty():
        c.note = "No price data"
        return c
    p = prices.filter(pl.col("date") <= ref).sort("date")
    if p.is_empty():
        c.note = "No price data before the reference date"
        return c
    last = p.row(-1, named=True)
    inputs: dict[str, Any] = {"close": last["close"], "close_date": last["date"]}
    if last.get("nav_per_share"):
        pnav = last["close"] / last["nav_per_share"]
        inputs.update(pnav=pnav, nav_per_share=last["nav_per_share"])
        c.score = ramp(1 - pnav, **cfg["nav_discount"])
    else:
        peak = p.filter(pl.col("date") >= ref - timedelta(days=365))["close"].max()
        if not peak:
            c.note = "Price data incomplete"
            return c
        inputs["drawdown_52w"] = 1 - last["close"] / peak
        c.score = ramp(inputs["drawdown_52w"], **cfg["drawdown_52w"])
        c.note = "NAV per share not available; fall from the 52-week high instead"
    c.available = True
    c.inputs = inputs
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
    min_cov = ms.get("min_coverage", 0.6)
    fins = tables["financials"].sort("period_end")
    latest = {r["org_nr"]: r for r in fins.filter(pl.col("period_end") <= ref).to_dicts()}
    live_b = tables["bond"].filter(pl.col("maturity") > ref)
    covs = tables["covenant"].join(live_b.select("isin", "org_nr"), on="isin")
    events_df = tables["event"].filter(pl.col("date") <= ref)
    prices = tables["price"]
    nom = "nominal_sek" if "nominal_sek" in live_b.columns else "nominal"
    bond_due_12m = {
        r["org_nr"]: r["due"] or 0.0
        for r in live_b.group_by("org_nr")
        .agg(pl.col(nom).filter(pl.col("maturity") <= ref + timedelta(days=365)).sum().alias("due"))
        .to_dicts()
    }
    out: dict[str, ScoreCard] = {}
    for co in tables["company"].to_dicts():
        org = co["org_nr"]
        fin = latest.get(org)
        evs = events_df.filter(pl.col("org_nr") == org).to_dicts()
        ticker = co["listed_ticker"]
        has_bonds = org in bond_due_12m

        refi = refinancing(fin, ms["refinancing"], bond_due_12m.get(org))
        if has_bonds:
            cov, cov_rows = covenant(
                fin, covs.filter(pl.col("org_nr") == org).to_dicts(), ms["covenant"]
            )
        else:
            cov, cov_rows = _not_applicable("covenant", "No bonds outstanding"), []
        lev = leverage(fin, ms["leverage"])
        if events_covered:
            ev = events(evs, ref, ms["events"])
        else:
            ev = Component("events", COMPONENT_LABELS["events"], 0.0, False)
            ev.note = "Event sources not connected yet"
        if ticker:
            mkt = market(prices.filter(pl.col("ticker") == ticker), ref, ms["market"])
        else:
            mkt = _not_applicable("market", "Not listed")
        comps = [refi, cov, lev, ev, mkt]
        for c in comps:
            c.weight = ms["weights"][c.key] if c.applicable else 0.0

        score, coverage = _reweight(comps)
        flags = [
            f"{c.label}: {c.note or 'no data'}" for c in comps if c.applicable and not c.available
        ]
        if score is not None and coverage < min_cov:
            score = None
            flags.append(
                f"Not scored: data covers {coverage:.0%} of the weight, "
                f"below the {min_cov:.0%} minimum"
            )
        if fin and (fin.get("confidence") or 1) < cfg["extraction"]["min_confidence"]:
            flags.append("Some reported figures are unverified (low extraction confidence)")

        fit, fit_comps = strategy_fit(co, fin, cfg["strategy_fit"])
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
