"""Extraction mapping and feed helpers. No network, no LLM calls."""

from datetime import date, datetime

import pytest

from headroom.extract import events, kpi
from headroom.extract.kpi import Figure, MaturityBucket, ReportKPIs, Share
from headroom.sources import newsfeeds
from headroom.sources.newsfeeds import Release


def report(**kw) -> ReportKPIs:
    base = dict(
        is_financial_report=True,
        company_name="X",
        period_end="2026-06-30",
        currency="SEK",
        interest_period_months=6,
        figures=[],
        debt_maturities=[],
        segments=[],
        regions=[],
    )
    base.update(kw)
    return ReportKPIs(**base)


def fig(field, value, conf=0.95, page=3):
    return Figure(field=field, value=value, page=page, confidence=conf, evidence="...")


def test_debt_due_calendar_buckets_spread_evenly():
    b = [
        MaturityBucket(
            kind="calendar_year", year=2026, months_from=0, months_to=0, amount=100, page=1
        ),
        MaturityBucket(
            kind="calendar_year", year=2027, months_from=0, months_to=0, amount=1200, page=1
        ),
        MaturityBucket(
            kind="calendar_year", year=2028, months_from=0, months_to=0, amount=1200, page=1
        ),
    ]
    due12 = kpi.debt_due(b, date(2026, 6, 30), 12)
    # rest of 2026 in full + about half of 2027
    assert due12 == pytest.approx(100 + 600, rel=0.05)
    assert kpi.debt_due(b, date(2026, 6, 30), 24) == pytest.approx(100 + 1200 + 600, rel=0.05)


def test_debt_due_month_buckets():
    b = [
        MaturityBucket(kind="months", year=0, months_from=0, months_to=12, amount=500, page=1),
        MaturityBucket(kind="months", year=0, months_from=12, months_to=24, amount=800, page=1),
    ]
    assert kpi.debt_due(b, date(2026, 6, 30), 12) == 500
    assert kpi.debt_due(b, date(2026, 6, 30), 24) == 1300


def test_to_rows_units_and_derivations():
    k = report(
        figures=[
            fig("property_value", 10_000),
            fig("interest_bearing_debt", 5_500),
            fig("cash", 500),
            fig("icr", 2.0),
            fig("net_interest_expense", 100),
            fig("ltv_pct", 50.0),
            fig("fixed_or_hedged_share_pct", 60.0, conf=0.6),
        ],
        segments=[
            Share(name="Residential", share_pct=75, page=4),
            Share(name="Lager/logistik", share_pct=25, page=4),
        ],
    )
    row, prov, enrich = kpi.to_rows(k, "556000-0000", "https://x/report.pdf", fx=1.0)
    assert row["ltv"] == 0.5 and row["fixed_share"] == 0.6
    assert row["net_debt"] == 5_000  # derived: debt - cash
    assert row["interest_expense"] == 200  # six-month figure annualised
    assert row["ebitda"] == 400  # ICR x interest
    assert row["confidence"] == 0.6  # weakest field
    methods = {p["field"]: p["method"] for p in prov}
    assert methods["net_debt"].startswith("derived")
    assert methods["interest_expense"].startswith("derived: 6-month")
    assert (
        '"residential": 0.75' in enrich["segment_mix"]
        and '"logistics": 0.25' in enrich["segment_mix"]
    )


def test_to_rows_converts_currency():
    k = report(currency="EUR", figures=[fig("property_value", 1_000)])
    row, _, _ = kpi.to_rows(k, "556000-0000", "u", fx=11.0)
    assert row["property_value"] == 11_000


def test_ltv_derived_when_not_reported():
    k = report(figures=[fig("property_value", 1_000), fig("net_debt", 450)])
    row, prov, _ = kpi.to_rows(k, "556000-0000", "u", fx=1.0)
    assert row["ltv"] == pytest.approx(0.45)
    assert any(p["field"] == "ltv" and p["method"].startswith("derived") for p in prov)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Interim report January - June 2026", True),
        ("Delårsrapport januari–september 2026", True),
        ("Invitation to presentation of interim report Q3", False),
        ("Bokslutskommuniké 2025", True),
        ("Sagax invests SEK 630 million", False),
        ("Heimstaden AB – Q2 2026 Results", True),
        (
            "Stenhus Fastigheter ökar förvaltningsresultatet med 20 % för perioden januari-juni 2026",
            True,
        ),
        ("Stenhus redovisar sitt starkaste första kvartal någonsin", True),
        ("okade hyror och minskade vakanser vasakronans halvarsrapport januari juni 2008", True),
        (
            "Inbjudan till presentation av Stenhus Fastigheters delårsrapport januari-juni 2026",
            False,
        ),
        ("Willhems års- och hållbarhetsredovisning 2025", False),
    ],
)
def test_is_period_report(title, expected):
    assert newsfeeds.is_period_report(title) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Holmström Fastigheter Holding AB (publ) initierar ett skriftligt förfarande", True),
        ("Company X receives waiver from bondholders", True),
        ("Ny ledamot i styrelsen", False),
        ("Interim report January - June 2026", False),
    ],
)
def test_event_candidates(title, expected):
    assert events.is_candidate(title) is expected


def test_yahoo_ticker_prefers_b_shares_and_skips_prefs():
    assert (
        newsfeeds.yahoo_ticker(["XSTO:SAGA A", "XSTO:SAGA B", "XSTO:SAGA D", "XLON:0QDX"])
        == "SAGA-B.ST"
    )
    assert newsfeeds.yahoo_ticker(["XSTO:WIHL"]) == "WIHL.ST"
    assert newsfeeds.yahoo_ticker(["XSTO:XYZ PREF"]) is None
    assert newsfeeds.yahoo_ticker([]) is None


def test_mfn_listing_parse():
    page = """
    <div onclick="goToNewsItem(event, '/a/acme/interim-report-q2-2026')">
      <a title="Interim report Q2 2026">x</a> 2026-08-28 08:00
      <a href="https://storage.mfn.se/abc/interim-report-q2-2026.pdf">pdf</a></div>
    <div onclick="goToNewsItem(event, '/a/acme/new-board')"><a title="New board">x</a></div>
    """

    class F:
        def get(self, url, **kw):
            class R:
                text = page

            return R()

    feed = newsfeeds.Feed("556000-0000", "mfn", "acme", "https://mfn.se/all/a/acme", "Acme")
    rels = newsfeeds.mfn_releases(F(), feed)
    assert [r.title for r in rels] == ["Interim report Q2 2026", "New board"]
    assert rels[0].attachments == ["https://storage.mfn.se/abc/interim-report-q2-2026.pdf"]
    assert rels[0].published == datetime(2026, 8, 28, 8, 0)


def test_event_language_dedupe():
    rel = Release("556000-0000", "mfn", "u", "t", datetime(2026, 7, 14), [])
    a = events.to_event(
        rel,
        events.Classified(event_type="written_procedure", severity=3, headline="Written procedure"),
    )
    b = dict(a, title="Skriftligt förfarande", source_url="u2")
    assert len(events.dedupe_languages([a, b])) == 1


def test_memo_citation_check():
    from headroom.extract.memo import Fact, Memo, Section, check_citations, to_markdown

    facts = [Fact("S1", "LTV 72%.", "u1"), Fact("S2", "Bond due 2026.", "u2")]
    memo = Memo(
        title="T",
        sections=[
            Section(
                heading="Situation",
                text="LTV is 72% [S1]. A bond falls due [S2]. Banks are nervous. Rates fell [S9].",
            )
        ],
    )
    bad = check_citations(memo, facts)
    assert bad == ["Banks are nervous.", "Rates fell [S9]."]
    md = to_markdown(memo, facts, bad)
    assert "Unsupported sentences" in md and "[S1] LTV 72%." in md and "Draft" in md


def test_is_annual_report():
    assert newsfeeds.is_annual_report("Willhems års- och hållbarhetsredovisning 2025")
    assert newsfeeds.is_annual_report("Vasakronan publicerar årsredovisningen för 2025")
    assert not newsfeeds.is_annual_report("Bokslutskommuniké 2025")
    assert not newsfeeds.is_annual_report("Kallelse till årsstämma")


def test_cision_name_match_and_ids():
    assert newsfeeds._name_match("Vasakronan", "Vasakronan AB (publ)")
    assert newsfeeds._name_match("Humlegården fastigheter AB", "Humlegården Fastigheter AB")
    assert not newsfeeds._name_match("Fastigheter", "Humlegården Fastigheter AB")
    assert "vasakronan" in newsfeeds._cision_slugs("Vasakronan AB (publ)")
    assert newsfeeds._cision_id("https://news.cision.com/se/x/r/report,c4371922") == 4371922


def test_llm_store_roundtrip_and_migration(tmp_path, monkeypatch):
    from headroom.extract import llm

    monkeypatch.setattr(llm, "LLM_STORE", tmp_path / "llm.jsonl")
    monkeypatch.setattr(llm, "LLM_CACHE", tmp_path / "llm")
    monkeypatch.setattr(llm, "_memo", None)
    (tmp_path / "llm").mkdir()
    (tmp_path / "llm" / "abc.json").write_text('{"output": {"x": 1}, "meta": {}}', "utf-8")
    assert llm.migrate_legacy() == 1
    llm._save("def", {"output": {"x": 2}, "meta": {}})
    monkeypatch.setattr(llm, "_memo", None)  # reload from disk
    assert llm._lookup("abc")["output"] == {"x": 1}
    assert llm._lookup("def")["output"] == {"x": 2}
