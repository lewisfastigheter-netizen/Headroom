import pytest

from headroom.schemas import luhn_ok, normalise_org_nr


@pytest.mark.parametrize("raw", ["5560360793", "556036-0793", "16556036-0793", "165560360793"])
def test_normalise_org_nr(raw):
    assert normalise_org_nr(raw) == "556036-0793"


def test_normalise_rejects_garbage():
    with pytest.raises(ValueError):
        normalise_org_nr("12345")


def test_demo_org_nr_passes_through():
    assert normalise_org_nr("DEMO-001") == "DEMO-001"


def test_luhn():
    # Skatteverket's published example personnummer uses the same check-digit rule
    assert luhn_ok("811218-9876")
    assert not luhn_ok("811218-9875")
