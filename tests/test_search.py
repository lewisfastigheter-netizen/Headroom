from headroom.model.search import search

ITEMS = [
    ("AB Sagax", "sagax"),
    ("Samhällsbyggnadsbolaget i Norden AB", "sbb"),
    ("Diös Fastigheter AB", "dios"),
    ("Stockholm · city", "sthlm"),
    ("Fabege AB", "fabege"),
]


def values(q):
    return [v for _, v in search(q, ITEMS)]


def test_close_matches_only():
    assert values("saga") == ["sagax"]
    assert values("sagga") == ["sagax"]  # one typo
    assert values("dios") == ["dios"]  # accents ignored
    assert values("stock") == ["sthlm"]
    assert values("fabgee") == ["fabege"]  # swapped letters
    assert values("xyzq") == []
    assert "sbb" not in values("sag")
