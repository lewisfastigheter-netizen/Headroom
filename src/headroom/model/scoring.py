"""Motivated-seller score for companies.

How a score is built (the same for companies and for places)
------------------------------------------------------------
1. Each part turns one figure into 0-100 on a straight line: the `zero` value in
   config/weights.yaml scores 0, the `full` value scores 100, anything beyond is clipped.
2. The score is the weighted average of the parts.
3. A part without data is left out and the other parts count for more. Nothing is
   imputed.

The motivated-seller score rests mainly on leverage, with a small part for deal size:

    ltv   loan-to-value: net debt / property value
    icr   interest cover: EBIT / net interest
    size  property value; full score inside the target band (SEK 50-250m by default),
          fading to zero outside it

Bond covenants are still read and shown on the company page, but do not feed the score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from headroom.config import weights as load_weights

COMPONENT_LABELS = {
    "ltv": "Loan-to-value",
    "icr": "Interest cover",
    "size": "Property value",
}


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
    headroom: float | None
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
    coverage: float  # share of the weight with data

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


def _headroom(test: str, actual: float | None, threshold: float) -> float | None:
    """Distance to the covenant as a share of the threshold; negative means in breach."""
    if actual is None or threshold == 0:
        return None
    if test == "ltv":  # maximum test
        return (threshold - actual) / threshold
    return (actual - threshold) / threshold  # minimum tests


# --------------------------------------------------------------------------- components


def covenant_rows(fin: dict | None, covs: list[dict]) -> list[CovenantRow]:
    """Each bond covenant with today's headroom. Shown on the company page, not scored."""
    metric = {"ltv": "ltv", "icr": "icr", "equity_ratio": "equity_ratio", "min_liquidity": "cash"}
    rows = []
    for cv in covs:
        actual = (fin or {}).get(metric[cv["test"]]) if cv["test"] in metric else None
        rows.append(
            CovenantRow(
                isin=cv["isin"],
                test=cv["test"],
                kind=cv["kind"],
                threshold=cv["threshold"],
                actual=actual,
                headroom=_headroom(cv["test"], actual, cv["threshold"]),
                source_url=cv["source_url"],
                page=cv.get("page"),
                confidence=cv.get("confidence", 1.0),
            )
        )
    return rows


# Reported values outside these bounds are treated as misread and not scored.
PLAUSIBLE = {"ltv": (0.0, 1.5), "icr": (-20.0, math.inf)}


def ratio_part(key: str, fin: dict | None, cfg: dict) -> Component:
    """LTV or ICR as reported, on the straight line from `zero` to `full`."""
    c = Component(key, COMPONENT_LABELS[key], 0.0, False)
    v = (fin or {}).get(key)
    if v is None:
        c.note = f"{'LTV' if key == 'ltv' else 'ICR'} not reported"
        return c
    lo, hi = PLAUSIBLE[key]
    if not lo <= v <= hi:
        # The same bounds the Companies table flags with "Check": a figure a property
        # company's accounts cannot produce is a misread, and would top the ranking.
        name = "LTV" if key == "ltv" else "ICR"
        c.note = f"Reported {name} outside the plausible range; probably misread"
        c.inputs = {key: v}
        return c
    c.available = True
    c.score = ramp(v, cfg["zero"], cfg["full"])
    c.inputs = {key: v}
    return c


def size(fin: dict | None, company: dict, cfg: dict) -> Component:
    """Full score inside the target band of property value, a straight fade to zero at the
    outer bounds."""
    c = Component("size", COMPONENT_LABELS["size"], 0.0, False)
    v = (fin or {}).get("property_value") or company.get("size")
    if v is None:
        c.note = "Property value not reported"
        return c
    if v < cfg["band_low"]:
        c.score = ramp(v, cfg["outer_low"], cfg["band_low"])
    elif v > cfg["band_high"]:
        c.score = ramp(v, cfg["outer_high"], cfg["band_high"])
    else:
        c.score = 100.0
    c.available = True
    c.inputs = {"property_value": v}
    return c


# --------------------------------------------------------------------------- driver


def reference_date(meta: dict) -> date:
    as_of = meta.get("as_of") or {}
    if meta.get("fictional") and as_of:
        return date.fromisoformat(max(as_of.values()))
    return date.today()


def score_all(tables: dict[str, pl.DataFrame], ref: date) -> dict[str, ScoreCard]:
    """Score every company on its latest reported figures up to `ref`."""
    cfg = load_weights()
    ms = cfg["motivated_seller"]
    min_cov = ms.get("min_coverage", 0.55)
    fins = tables["financials"].sort("period_end")
    latest = {r["org_nr"]: r for r in fins.filter(pl.col("period_end") <= ref).to_dicts()}
    live_b = tables["bond"].filter(pl.col("maturity") > ref)
    covs = tables["covenant"].join(live_b.select("isin", "org_nr"), on="isin")
    cov_by_org: dict[str, list[dict]] = {}
    for cv in covs.to_dicts():
        cov_by_org.setdefault(cv["org_nr"], []).append(cv)
    out: dict[str, ScoreCard] = {}
    for co in tables["company"].to_dicts():
        org = co["org_nr"]
        fin = latest.get(org)
        comps = [
            ratio_part("ltv", fin, ms["ltv"]),
            ratio_part("icr", fin, ms["icr"]),
            size(fin, co, ms["size_sek_m"]),
        ]
        for c in comps:
            c.weight = ms["weights"][c.key]
        score, coverage = _reweight(comps)
        flags = [f"{c.label}: {c.note or 'no data'}" for c in comps if not c.available]
        if score is not None and coverage < min_cov:
            score = None
            flags.append(
                f"Not scored: data covers {coverage:.0%} of the weight, "
                f"below the {min_cov:.0%} minimum"
            )
        if fin and (fin.get("confidence") or 1) < cfg["extraction"]["min_confidence"]:
            flags.append("Some reported figures are unverified (low extraction confidence)")
        out[org] = ScoreCard(
            org, score, comps, flags, covenant_rows(fin, cov_by_org.get(org, [])), coverage
        )
    return out


def score_frame(cards: dict[str, ScoreCard]) -> pl.DataFrame:
    rows = []
    for org, c in cards.items():
        row = {"org_nr": org, "score": c.score, "coverage": c.coverage, "n_flags": len(c.flags)}
        for comp in c.components:
            row[f"s_{comp.key}"] = comp.score if comp.available else None
        heads = [r.headroom for r in c.covenants if r.headroom is not None]
        row["min_headroom"] = min(heads) if heads else None
        rows.append(row)
    return pl.DataFrame(
        rows,
        schema_overrides={
            "score": pl.Float64,
            "coverage": pl.Float64,
            "s_ltv": pl.Float64,
            "s_icr": pl.Float64,
            "s_size": pl.Float64,
            "min_headroom": pl.Float64,
        },
    )
