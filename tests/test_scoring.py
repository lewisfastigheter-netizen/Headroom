from datetime import date

import polars as pl
import pytest

from headroom.demo.generate import REFERENCE_DATE, build
from headroom.model.scoring import (
    Component,
    _headroom,
    _reweight,
    ramp,
    score_all,
    weighted_average,
)


def test_ramp_both_directions():
    assert ramp(0.55, 0.40, 0.70) == pytest.approx(50)
    assert ramp(2.0, 3.0, 1.0) == pytest.approx(50)  # lower ICR is worse
    assert ramp(10, 0, 1) == 100 and ramp(-1, 0, 1) == 0


def test_ramp_log_scale_adds_same_points_per_doubling():
    one = ramp(1.0, 0.5, 5.0, scale="log")
    two = ramp(2.0, 0.5, 5.0, scale="log")
    four = ramp(4.0, 0.5, 5.0, scale="log")
    assert two - one == pytest.approx(four - two) == pytest.approx(30.1, abs=0.1)
    assert ramp(0.0, 0.5, 5.0, scale="log") == 0


def test_weighted_average_leaves_out_missing():
    assert weighted_average([(0.5, 80), (0.3, None), (0.2, 20)]) == (
        pytest.approx((80 * 0.5 + 20 * 0.2) / 0.7),
        pytest.approx(0.7),
    )
    assert weighted_average([(1.0, None)]) == (None, 0.0)


def test_headroom_signs():
    assert _headroom("ltv", 0.60, 0.75) == pytest.approx(0.2)  # max test
    assert _headroom("icr", 1.5, 2.0) == pytest.approx(-0.25)  # min test, in breach


def test_missing_component_is_reweighted_not_imputed():
    comps = [
        Component("a", "A", 0.5, True, score=80),
        Component("b", "B", 0.3, False),
        Component("c", "C", 0.2, True, score=20),
    ]
    score, coverage = _reweight(comps)
    assert comps[1].effective_weight == 0
    assert comps[0].effective_weight == pytest.approx(0.5 / 0.7)
    assert score == pytest.approx((80 * 0.5 + 20 * 0.2) / 0.7)
    assert coverage == pytest.approx(0.7)


@pytest.fixture(scope="module")
def cards():
    return score_all(build(), REFERENCE_DATE)


def test_all_scores_in_range(cards):
    for c in cards.values():
        assert c.score is None or 0 <= c.score <= 100
        assert c.fit is None or 0 <= c.fit <= 100


def test_covenant_and_market_do_not_apply_without_bonds_or_shares(cards):
    tables = build()
    private = tables["company"].filter(pl.col("tier") == "private")["org_nr"]
    for org in private:
        card = cards[org]
        for key in ("covenant", "market"):
            c = card.component(key)
            assert not c.applicable and c.effective_weight == 0
        # not applicable is not a data gap
        assert not any(f.startswith(("Covenant", "Share price")) for f in card.flags)


def test_wind_down_case_is_caught(cards):
    # The demo issuer modelled on a bondholder-led wind-down must rank as such.
    card = cards["DEMO-009"]
    assert card.opportunity == "Bondholder-led wind-down"
    assert card.score >= 75


def test_healthy_issuer_scores_low(cards):
    assert cards["DEMO-005"].score < 40


def test_events_count_only_the_last_12_months():
    from headroom.config import weights
    from headroom.model.scoring import events

    cfg = weights()["motivated_seller"]["events"]
    ev = [
        {"date": date(2026, 1, 1), "type": "waiver", "title": "x"},
        {"date": date(2026, 6, 1), "type": "written_procedure", "title": "y"},
    ]
    both = events(ev, date(2026, 12, 1), cfg).score
    one = events(ev, date(2027, 3, 1), cfg).score  # the waiver is now over a year old
    pts = cfg["points"]
    assert both == min(pts["waiver"] + pts["written_procedure"], cfg["cap"])
    assert one == pts["written_procedure"]


def test_refinancing_ratio_is_12_month_debt_over_liquidity():
    from headroom.config import weights
    from headroom.model.scoring import refinancing

    cfg = weights()["motivated_seller"]["refinancing"]
    c = refinancing({"debt_due_12m": 200.0, "cash": 50.0, "undrawn_facilities": 50.0}, cfg)
    assert c.inputs["ratio"] == pytest.approx(2.0)
    assert c.score == pytest.approx(ramp(2.0, cfg["zero"], cfg["full"], scale="log"))
    no_undrawn = refinancing({"debt_due_12m": 100.0, "cash": 100.0}, cfg)
    assert "undrawn" in no_undrawn.note.lower()
