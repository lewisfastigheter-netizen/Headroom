"""Smoke test: every page renders without an exception, in demo and (if present) live mode."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "streamlit_app.py"
MODES = ["demo"] + (["live"] if (ROOT / "data/snapshots/live/bond.parquet").exists() else [])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("page", ["companies", "locations", "underwriting", "issuer", "method"])
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
@pytest.mark.parametrize(
    "state",
    [
        {"loc_level": "Kommun", "loc_areas_kommun": ["1480", "1481"]},
        {"loc_level": "Län", "loc_areas_lan": ["12"]},
        {"loc_level": "RegSO", "loc_kommun": "Göteborg"},
        {"loc_level": "RegSO", "loc_areas_regso": ["0380R016"]},
        {"loc_level": "RegSO"},
    ],
)
def test_location_start_levels(state, monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", "demo")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["mode"] = "demo"
    at.run()
    for k, v in state.items():
        at.session_state[k] = v
    at.switch_page("views/locations.py").run()
    assert not at.exception, at.exception


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


@pytest.mark.parametrize(
    "state",
    [
        {"uw_kommun": "1480", "uw_segment": "logistics"},
        {"uw_kommun": "0380", "uw_segment": "light_industrial"},
        {"uw_kommun": "1280", "uw_segment": "hotel"},
        {"uw_kommun": "0180", "uw_segment": "retail"},
        {"uw_org": "559286-6809", "uw_segment": "residential"},
    ],
)
def test_underwriting_states(state, monkeypatch):
    mode = MODES[-1]
    monkeypatch.setenv("HEADROOM_MODE", mode)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["mode"] = mode
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    at.switch_page("views/underwriting.py").run()
    assert not at.exception, at.exception
