"""Smoke test: every page renders without an exception, in demo and (if present) live mode."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "streamlit_app.py"
MODES = ["demo"] + (["live"] if (ROOT / "data/snapshots/live/bond.parquet").exists() else [])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("page", ["market", "companies", "locations", "issuer", "method"])
def test_page_renders(page, mode, monkeypatch):
    monkeypatch.setenv("HEADROOM_MODE", mode)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.session_state["mode"] = mode
    at.run()
    if page != "market":
        at.switch_page(f"views/{page}.py").run()
    assert not at.exception, at.exception


LOC = ROOT / "data/snapshots/locations/location.parquet"


@pytest.mark.skipif(not LOC.exists(), reason="no location snapshot")
@pytest.mark.parametrize(
    "level,code",
    [("kommun", "0380"), ("lan", "14"), ("regso", "0380R016"), ("tatort", "0380TC125")],
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
