import json

from headroom.model import geo


def test_address_to_county_and_city():
    assert geo.from_address("STOCKHOLM", "11220") == ("Stockholms län", "Stockholm")
    # postal town that is not a municipality: county from the postcode prefix
    assert geo.from_address("VÄSTRA FRÖLUNDA", "421 31") == (
        "Västra Götalands län",
        "Västra Frölunda",
    )


def test_majority_region_names_a_place():
    assert geo.from_regions(json.dumps({"Gothenburg": 0.6, "Stockholm": 0.4})) == (
        "Västra Götalands län",
        "Göteborg",
    )
    assert geo.from_regions(json.dumps({"Central Stockholm": 0.7})) == (
        "Stockholms län",
        "Stockholm",
    )
    assert geo.from_regions(json.dumps({"Norge": 0.7})) == (None, None)  # countries are not placed
    assert geo.from_regions(None) == (None, None)
