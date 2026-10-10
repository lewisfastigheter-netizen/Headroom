from headroom.store.private import group_of, is_property_company


def test_main_sni_decides():
    assert is_property_company("68.201, 68.203", "Thulehus Växjö AB")
    assert not is_property_company("23.640, 68.203, 08.120", "Aktiebolaget Bösarps Grus & Torrbruk")
    assert not is_property_company("02.101, 68.202", "Brattby Skog Aktiebolag")
    # a property name keeps a company whose main code is something else
    assert is_property_company("77.390, 01.430, 68.202", "Mariesjö Fastighets Aktiebolag")
    assert not is_property_company(None, "Psykiatricentrum i Vittsjö AB")


def test_group_of_matches_parent_brand_at_start():
    assert group_of("Balder Sundsbron AB") == "Fastighets AB Balder"
    assert group_of("Logistea Transformatorn 2 AB") == "Logistea AB"
    assert group_of("Fastighets AB Balder") is None  # the parent itself is not an SPV
    assert group_of("Thulehus Växjö AB") is None
