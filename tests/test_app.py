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
