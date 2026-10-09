"""Rate-shock arithmetic.

shocked ICR = EBITDA / (interest + floating debt x shock)

Floating debt is gross debt times (1 - fixed share), where the fixed share is
the share of debt the company reports as fixed-rate or hedged. When the fixed
share is not reported, all debt is treated as floating, which is the
conservative case; the UI states which assumption was used.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShockResult:
    bp: float
    icr: float | None
    floating_debt: float | None
    extra_interest: float | None
    assumption: str


def floating_debt(gross_debt: float | None, fixed_share: float | None) -> tuple[float | None, str]:
    if gross_debt is None:
        return None, "Gross debt not available"
    if fixed_share is None:
        return gross_debt, "Fixed or hedged share not reported: all debt treated as floating"
    share = min(max(fixed_share, 0.0), 1.0)
    return gross_debt * (
        1 - share
    ), f"Floating share {1 - share:.0%} of gross debt, from reported hedging"


def shocked_icr(
    ebitda: float | None,
    interest: float | None,
    gross_debt: float | None,
    fixed_share: float | None,
    bp: float,
) -> ShockResult:
    flt, assumption = floating_debt(gross_debt, fixed_share)
    if ebitda is None or interest is None or flt is None:
        return ShockResult(bp, None, flt, None, assumption)
    extra = flt * bp / 10_000
    denom = interest + extra
    icr = ebitda / denom if denom > 0 else None
    return ShockResult(bp, icr, flt, extra, assumption)


def breakeven_bp(
    ebitda: float | None,
    interest: float | None,
    gross_debt: float | None,
    fixed_share: float | None,
    covenant: float,
) -> float | None:
    """Shock in bp at which the shocked ICR falls to the covenant level.

    Returns 0 if already in breach, None if the floating debt is zero or inputs
    are missing.
    """
    flt, _ = floating_debt(gross_debt, fixed_share)
    if ebitda is None or interest is None or not flt:
        return None
    # ebitda / (interest + flt*x) = covenant  ->  x = (ebitda/covenant - interest) / flt
    x = (ebitda / covenant - interest) / flt
    return max(x * 10_000, 0.0)
