"""Headroom: entry point and navigation.

Run with `make app` or `uv run streamlit run app/streamlit_app.py`.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Headroom",
    layout="wide",
    initial_sidebar_state="collapsed",
)

import ui.plotly_template  # noqa: E402,F401  registers the Plotly template
from ui.components import inject_css, topline  # noqa: E402
from ui.data import get_data  # noqa: E402

inject_css()
st.logo(str(Path(__file__).parent / "static" / "wordmark.svg"), size="large")

data = get_data()

pages = [
    st.Page("views/market.py", title="Market", url_path="market", default=True),
    st.Page("views/companies.py", title="Companies", url_path="companies"),
    st.Page("views/search.py", title="Search", url_path="search"),
    st.Page("views/locations.py", title="Locations", url_path="locations"),
    st.Page("views/method.py", title="Method", url_path="method"),
    # Company pages open from the tables; they are not a menu item.
    st.Page("views/issuer.py", title="Company", url_path="issuer", visibility="hidden"),
]
nav = st.navigation(pages, position="top")

nav.run()

# Data notice at the foot of every page.
as_of = max((data.meta.get("as_of") or {}).values(), default=None)
st.write("")
topline(as_of, data.fictional)
