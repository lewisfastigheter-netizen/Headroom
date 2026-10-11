"""Smoke test: every page renders without an exception, in demo and (if present) live mode."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "streamlit_app.py"
MODES = ["demo"] + (["live"] if (ROOT / "data/snapshots/live/bond.parquet").exists() else [])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("page", ["companies", "locations", "issuer", "method"])
def test_page_renders(page, mode, monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", mode)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.session_state["mode"] = mode
    at.run()
    if page != "companies":
        at.switch_page(f"views/{page}.py").run()
    assert not at.exception, at.exception


LOC = ROOT / "data/snapshots/locations/location.parquet"


@pytest.mark.skipif(not LOC.exists(), reason="no location snapshot")
@pytest.mark.parametrize(
    "level,code",
    [("kommun", "0380"), ("lan", "14"), ("regso", "0380R016")],
)
def test_location_place_renders(level, code, monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", "demo")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["mode"] = "demo"
    at.run()
    at.query_params["level"] = level
    at.query_params["code"] = code
    at.switch_page("views/locations.py").run()
    assert not at.exception, at.exception


@pytest.mark.skipif(not LOC.exists(), reason="no location snapshot")
def test_location_start_is_only_a_kommun_search(monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", "demo")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["mode"] = "demo"
    at.run()
    at.switch_page("views/locations.py").run()
    assert not at.exception, at.exception
    assert not at.selectbox and not at.multiselect  # no filters left on the start page
    assert not at.get("plotly_chart")


def test_kommun_index_holds_only_municipalities():
    from headroom.model import location as L
    from headroom.store.db import open_snapshot

    if not LOC.exists():
        pytest.skip("no location snapshot")
    idx = L.place_index(open_snapshot("locations").table("location"))
    kommuner = [p for p in idx if p["value"].startswith("kommun:")]
    assert len(kommuner) == 290


@pytest.mark.skipif(not LOC.exists(), reason="no location snapshot")
def test_tatort_is_not_a_page(monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", "demo")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["mode"] = "demo"
    at.run()
    at.query_params["level"] = "tatort"
    at.query_params["code"] = "0380TC125"
    at.switch_page("views/locations.py").run()
    assert not at.exception, at.exception
    html = " ".join(str(getattr(e, "proto", "")) for e in at.get("html"))
    assert "No place with code" in html
