from datetime import date

import polars as pl
import pytest

from headroom.demo.generate import REFERENCE_DATE, build
from headroom.model.scoring import Component, _headroom, _reweight, ramp, score_all


def test_ramp_both_directions():
    assert ramp(0.55, 0.40, 0.70) == pytest.approx(50)
    assert ramp(2.0, 3.0, 1.0) == pytest.approx(50)  # lower ICR is worse
    assert ramp(10, 0, 1) == 100 and ramp(-1, 0, 1) == 0


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


def test_private_companies_flag_missing_covenants(cards):
    tables = build()
    private = tables["company"].filter(pl.col("tier") == "private")["org_nr"]
    for org in private:
        card = cards[org]
        assert not card.component("covenant").available
        assert any("Covenant" in f for f in card.flags)
        assert not card.component("market").available


def test_wind_down_case_is_caught(cards):
    # The demo issuer modelled on a bondholder-led wind-down must rank as such.
    card = cards["DEMO-009"]
    assert card.opportunity == "Bondholder-led wind-down"
    assert card.score >= 75


def test_healthy_issuer_scores_low(cards):
    assert cards["DEMO-005"].score < 40


def test_events_decay():
    from headroom.config import weights
    from headroom.model.scoring import events

    cfg = weights()["motivated_seller"]["events"]
    ev = [{"date": date(2026, 1, 1), "type": "waiver", "title": "x"}]
    fresh = events(ev, date(2026, 1, 1), cfg).score
    old = events(ev, date(2026, 1, 1).replace(year=2027), cfg).score
    assert fresh == cfg["severity"]["waiver"]
    assert old < fresh / 2
