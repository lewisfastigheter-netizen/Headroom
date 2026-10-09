from headroom.model.stress import breakeven_bp, floating_debt, shocked_icr


def test_shock_formula():
    # EBITDA 300, interest 100, debt 2000 with 50% hedged: +100bp adds 10 of interest
    r = shocked_icr(300, 100, 2000, 0.5, 100)
    assert r.extra_interest == 10
    assert abs(r.icr - 300 / 110) < 1e-9


def test_zero_shock_is_reported_icr():
    assert shocked_icr(300, 100, 2000, 0.5, 0).icr == 3.0


def test_unreported_hedging_treated_as_all_floating():
    flt, note = floating_debt(1000, None)
    assert flt == 1000
    assert "all debt treated as floating" in note


def test_breakeven_matches_covenant():
    be = breakeven_bp(300, 100, 2000, 0.5, 2.0)
    assert abs(shocked_icr(300, 100, 2000, 0.5, be).icr - 2.0) < 1e-9


def test_breakeven_zero_when_already_in_breach():
    assert breakeven_bp(150, 100, 2000, 0.5, 2.0) == 0.0


def test_missing_inputs_give_none():
    assert shocked_icr(None, 100, 2000, 0.5, 100).icr is None
