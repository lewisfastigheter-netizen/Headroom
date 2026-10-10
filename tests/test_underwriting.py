import io
from dataclasses import replace

import pytest

from headroom.model import underwriting as uw


def test_irr_known_values():
    assert uw.irr([-100, 110]) == pytest.approx(0.10, abs=1e-9)
    assert uw.irr([-100, 0, 121]) == pytest.approx(0.10, abs=1e-9)
    assert uw.irr([100, 10]) is None  # no sign change


def test_unlevered_irr_equals_yield_plus_growth_without_costs():
    # Constant growth, exit at the entry yield, no costs: IRR = yield + growth (Gordon).
    a = uw.Assumptions(price=100, noi=5, rent_growth=0.02, ltv=0, exit_yield=0.05,
                       entry_cost=0, exit_cost=0, hold=10)  # fmt: skip
    r = uw.run(a)
    assert r.irr_unlevered == pytest.approx(0.07, abs=1e-6)
    assert r.irr_levered == pytest.approx(r.irr_unlevered, abs=1e-9)


def test_leverage_raises_irr_when_yield_exceeds_rate():
    base = uw.Assumptions(price=100, noi=6, ltv=0.0, interest=0.04, exit_yield=0.06)
    lev = replace(base, ltv=0.6)
    assert uw.run(lev).irr_levered > uw.run(base).irr_levered
    neg = replace(lev, interest=0.09)  # debt dearer than the yield: negative leverage
    assert uw.run(neg).irr_levered < uw.run(base).irr_levered


def test_solve_price_hits_target():
    a = uw.Assumptions(price=100, noi=5, ltv=0.5, interest=0.04, exit_yield=0.05)
    p = uw.solve_price(a, 0.12)
    assert p is not None
    assert uw.run(replace(a, price=p)).irr_levered == pytest.approx(0.12, abs=1e-4)
    assert p < 100  # 12% needs a lower price than a 5% yield with 2% growth gives


def test_sensitivity_shape_and_direction():
    a = uw.Assumptions(price=100, noi=5)
    grid = uw.sensitivity(a, [0.045, 0.05, 0.055], [0.0, 0.02])
    assert len(grid) == 3 and len(grid[0]) == 2
    assert grid[0][1] > grid[2][1]  # lower exit yield, higher IRR
    assert grid[1][1] > grid[1][0]  # more growth, higher IRR


def test_market_of():
    assert uw.market_of("0180", "residential", "0010") == "stockholm"
    assert uw.market_of("0186", "residential") == "stockholm"  # Stockholms län without metro code
    assert uw.market_of("1480", "logistics", "0020") == "gothenburg"
    assert uw.market_of("1280", "residential", "0030") == "malmo"
    assert uw.market_of("1280", "logistics", "0030") == "oresund"
    assert uw.market_of("1283", "logistics", "0060") == "oresund"  # Helsingborg, Skåne
    assert uw.market_of("1283", "residential", "0060") == "regional"
    assert uw.market_of("0380", "residential", "0060") == "regional"


def test_market_file_rows_have_sources():
    data = uw.market_data()
    for kind in ("prime_yields", "valuation_yields", "rents", "vacancy", "deals", "financing"):
        for r in data[kind]:
            assert r["source"] in data["sources"], (kind, r)
            assert data["sources"][r["source"]]["url"].startswith("https://")
    assert uw.pick("prime_yields", "logistics", "oresund")["value"] == 5.20
    assert uw.pick("rents", "light_industrial", "stockholm")["market"] == "sweden"


def test_excel_has_formulas():
    import zipfile

    a = uw.Assumptions(price=250, noi=12.5)
    xls = uw.to_excel(a, "Test", [("Exit yield", "C&W")])
    sheet = zipfile.ZipFile(io.BytesIO(xls)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f>IRR(" in sheet
    assert '<c r="B5" s=' in sheet and "<v>250</v>" in sheet
