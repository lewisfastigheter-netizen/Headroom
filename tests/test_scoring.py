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


def test_score_is_ltv_icr_and_size_only(cards):
    for c in cards.values():
        assert [x.key for x in c.components] == ["ltv", "icr", "size"]


def test_stretched_issuer_scores_high_and_healthy_low(cards):
    assert cards["DEMO-009"].score >= 75  # LTV 72%, ICR 1.0x
    assert cards["DEMO-005"].score < 40


def test_size_band_gives_full_points_inside_and_fades_outside():
    from headroom.config import weights
    from headroom.model.scoring import size

    cfg = weights()["motivated_seller"]["size_sek_m"]
    inside = size({"property_value": (cfg["band_low"] + cfg["band_high"]) / 2}, {}, cfg)
    assert inside.score == 100
    assert size({"property_value": cfg["outer_high"] * 2}, {}, cfg).score == 0
    assert size({"property_value": cfg["outer_low"] / 2}, {}, cfg).score == 0
    mid_high = (cfg["band_high"] + cfg["outer_high"]) / 2
    assert size({"property_value": mid_high}, {}, cfg).score == pytest.approx(50)


def test_size_alone_is_not_enough_to_score():
    tables = build()
    org = tables["company"]["org_nr"][0]
    fin = (
        tables["financials"]
        .filter(pl.col("org_nr") == org)
        .with_columns(pl.lit(None, pl.Float64).alias("ltv"), pl.lit(None, pl.Float64).alias("icr"))
    )
    tables["financials"] = pl.concat([tables["financials"].filter(pl.col("org_nr") != org), fin])
    card = score_all(tables, REFERENCE_DATE)[org]
    assert card.score is None
    assert any(f.startswith("Not scored") for f in card.flags)


def test_leverage_outweighs_size():
    from headroom.config import weights

    w = weights()["motivated_seller"]["weights"]
    assert w["ltv"] + w["icr"] >= 0.8
    assert sum(w.values()) == pytest.approx(1.0)
