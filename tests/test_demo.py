import polars as pl

from headroom.demo.generate import build


def test_demo_data_is_unmistakably_fictional():
    t = build()
    assert t["company"]["org_nr"].str.starts_with("DEMO-").all()
    assert t["bond"]["isin"].str.starts_with("SEDEMO").all()
    for name in ("company", "bond", "covenant", "financials", "event", "price", "rate"):
        assert t[name]["source_url"].str.starts_with("demo://").all(), name


def test_demo_has_all_tiers_and_low_confidence_fields():
    t = build()
    assert set(t["company"]["tier"]) == {"listed", "bond", "private"}
    assert (t["provenance"]["confidence"] < 0.8).any()


def test_bonds_join_to_companies():
    t = build()
    missing = t["bond"].join(t["company"], on="org_nr", how="anti")
    assert missing.height == 0
    assert t["financials"].filter(pl.col("ltv") > 1).height == 0
