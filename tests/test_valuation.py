from headroom.model import valuation as v
from headroom.sources import bolagsverket as bv


def test_classify_wording():
    assert v.classify("Förvaltningsfastigheternas verkliga värde uppgick till 12 345 Mkr") == v.FAIR
    assert v.classify("Fair value of investment properties SEK 8,078m") == v.FAIR
    assert v.classify("Bokfört värde byggnader och mark 412 Mkr") == v.BOOK
    assert v.classify("Fastighetsvärde 5 688 Mkr") == v.FAIR_PRESUMED
    assert v.classify("Fastighetsvärde 5 688 Mkr", default=None) is None


def test_annual_report_prefers_disclosed_fair_value():
    from datetime import date

    def fact(concept, value):
        return bv.Fact(concept=concept, value=value, context="c", period_end=date(2025, 12, 31))

    facts = [fact("ByggnaderMark", 400e6), fact("ForvaltningsfastigheterVerkligtVarde", 900e6)]
    a = bv.annual_figures(facts)
    assert a["property_value"] == 900 and a["value_basis"] == v.FAIR
    a = bv.annual_figures([fact("ByggnaderMark", 400e6)])
    assert a["property_value"] == 400 and a["value_basis"] == v.BOOK
    assert bv.to_financials("556000-0000", a, "x")["value_basis"] == v.BOOK
