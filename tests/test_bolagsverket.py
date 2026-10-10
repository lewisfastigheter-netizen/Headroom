"""Bolagsverket parsing on synthetic fixtures (the live API needs credentials)."""

from datetime import date

from headroom.sources import bolagsverket as bv

IXBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:se-gen-base="http://www.taxonomier.se/se/fr/gen-base/2021-10-31">
<body>
<ix:header><ix:resources>
  <xbrli:context id="balans0"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="period0"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="balans1"><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
</ix:resources></ix:header>
<ix:nonFraction name="se-gen-base:ByggnaderMark" contextRef="balans0" unitRef="SEK" scale="0" decimals="INF">250 000 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:ByggnaderMark" contextRef="balans1" unitRef="SEK" scale="0">240 000 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:OvrigaLangfristigaSkulderKreditinstitut" contextRef="balans0" unitRef="SEK" scale="3">120 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:OvrigaKortfristigaSkulderKreditinstitut" contextRef="balans0" unitRef="SEK" scale="3">60 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:KassaBankExklRedovisningsmedel" contextRef="balans0" unitRef="SEK">5 000 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:Rorelseresultat" contextRef="period0" unitRef="SEK" scale="3">12 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:RantekostnaderLiknandeResultatposter" contextRef="period0" unitRef="SEK" scale="3" sign="-">9 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:Soliditet" contextRef="balans0" unitRef="procent">18</ix:nonFraction>
</body></html>"""


def test_parse_ixbrl_and_map():
    a = bv.annual_figures(bv.parse_ixbrl(IXBRL))
    assert a["period_end"] == date(2025, 12, 31)
    assert a["property_value"] == 250.0  # SEK m, latest period only
    assert a["debt_credit_short"] == 60.0  # scale 3 -> thousands
    assert a["equity_ratio_pct"] == 0.18
    row = bv.to_financials("556000-0000", a, "src")
    assert row["gross_debt"] == 180.0
    assert row["ltv"] == (180.0 - 5.0) / 250.0
    assert row["debt_due_12m"] == 60.0
    assert round(row["icr"], 2) == round(12 / 9, 2)


def _facts(**kw):
    return [
        bv.Fact(concept=k, value=v * 1e6, context="c", period_end=date(2025, 12, 31))
        for k, v in kw.items()
    ]


def test_debt_lines_summed():
    """K2/K3 balance-sheet concepts: bank loans, overdraft and shareholder loans."""
    a = bv.annual_figures(
        _facts(
            ByggnaderMark=100,
            OvrigaLangfristigaSkulderKreditinstitut=50,
            OvrigaKortfristigaSkulderKreditinstitut=4,
            CheckrakningskreditKortfristig=1,
            SkulderKoncernforetagLangfristiga=10,
            KassaBank=5,
            RantekostnaderLiknandeResultatposter=-2.6,
            Rorelseresultat=6,
        )
    )
    row = bv.to_financials("556000-0000", a, "src")
    assert row["gross_debt"] == 65
    assert row["ltv"] == 0.6
    assert row["debt_due_12m"] == 5
    assert row["confidence"] == 0.95


def test_other_long_term_debt_as_fallback():
    a = bv.annual_figures(
        _facts(
            ByggnaderMark=40, OvrigaLangfristigaSkulder=30, RantekostnaderLiknandeResultatposter=-1
        )
    )
    row = bv.to_financials("556000-0000", a, "src")
    assert row["gross_debt"] == 30 and row["ltv"] == 0.75 and row["confidence"] == 0.8
    # without interest expense it is not taken as debt
    a = bv.annual_figures(_facts(ByggnaderMark=40, OvrigaLangfristigaSkulder=30))
    assert bv.to_financials("556000-0000", a, "src")["ltv"] is None


def test_sni_codes_found_anywhere():
    rec = {
        "organisation": {"naringsgrenOrganisation": {"sni": [{"kod": "68201"}, {"kod": "68.320"}]}}
    }
    assert bv.sni_codes(rec) == ["68.201", "68.320"]
    assert bv.is_real_estate(bv.sni_codes(rec))


def test_bulkfile_rows(tmp_path):
    p = tmp_path / "bulk.txt"
    p.write_text(
        "organisationsidentitet;organisationsnamn;organisationsform;avregistreringsdatum;"
        "pagandeAvvecklingsEllerOmstruktureringsforfarande;verksamhetsbeskrivning\n"
        "5560000000$1;Exempel Fastigheter AB;AB;;;Äga och förvalta fastigheter\n"
        "5560000001$1;Bageri AB;AB;;;Bageriverksamhet\n"
        "5560000002$1;Gammal Fastighet AB;AB;2020-01-01;;Fastighetsförvaltning\n",
        encoding="utf-8",
    )
    rows = bv.private_candidates(bv.read_bulkfile(p), exclude=set())
    assert [r["org_nr"] for r in rows] == ["556000-0000"]


def test_late_annual_report():
    assert bv.is_late(date(2024, 12, 31), date(2026, 8, 15))  # FY2025 report overdue
    assert not bv.is_late(date(2025, 12, 31), date(2026, 6, 1))


def test_sni_ignores_legal_form_and_blank_codes():
    rec = {
        "juridiskForm": {"kod": "49"},
        "naringsgrenOrganisation": {
            "sni": [{"kod": "68320"}, {"kod": "     "}, {"kod": "00000"}, {"kod": "69201"}]
        },
    }
    assert bv.sni_codes(rec) == ["68.320", "69.201"]


def test_private_candidates_wording_and_priority():
    rows = [
        {
            "org_nr": "1",
            "name": "Lindqvist Holding AB",
            "form": "AB",
            "description": "Bolaget ska äga och förvalta fast egendom samt bedriva uthyrning av lokaler",
        },
        {
            "org_nr": "2",
            "name": "Nordic Invest AB",
            "form": "AB",
            "description": "Förvaltning av värdepapper",
        },
        {"org_nr": "3", "name": "Bageri AB", "form": "AB", "description": "Bageriverksamhet"},
        {"org_nr": "4", "name": "X AB", "form": "AB", "description": "", "sni": "68203"},
        {
            "org_nr": "5",
            "name": "Y Fastigheter AB",
            "form": "AB",
            "description": "",
            "sni": "47110",
        },
    ]
    got = bv.private_candidates(iter(rows), exclude=set())
    assert [r["org_nr"] for r in got] == ["4", "1"]  # SNI first, then ownership wording


def test_private_ledger_skips_recent_negatives(tmp_path, monkeypatch):
    from headroom import pipeline

    monkeypatch.setattr(pipeline, "PRIVATE_LEDGER", tmp_path / "checked.csv")
    led = pipeline._Ledger(date(2026, 10, 1))
    led.mark("1", "not_real_estate")
    led.mark("2", "kept")
    led.save()
    led = pipeline._Ledger(date(2026, 12, 1))
    assert led.skip("1") and not led.skip("2") and led.kept("2") and not led.skip("3")
    assert not pipeline._Ledger(date(2027, 12, 1)).skip("1")  # re-checked after a year
