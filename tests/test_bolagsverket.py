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
<ix:nonFraction name="se-gen-base:SkulderKreditinstitutLangfristiga" contextRef="balans0" unitRef="SEK" scale="3">120 000</ix:nonFraction>
<ix:nonFraction name="se-gen-base:SkulderKreditinstitutKortfristiga" contextRef="balans0" unitRef="SEK" scale="3">60 000</ix:nonFraction>
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
