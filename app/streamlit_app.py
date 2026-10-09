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
from ui.data import available_modes, current_mode, get_data  # noqa: E402

inject_css()
st.logo(str(Path(__file__).parent / "static" / "wordmark.svg"), size="large")

data = get_data()

pages = [
    st.Page("views/market.py", title="Market", url_path="market", default=True),
    st.Page("views/screen.py", title="Screen", url_path="screen"),
    st.Page("views/issuer.py", title="Issuer", url_path="issuer"),
    st.Page("views/bonds.py", title="Bonds", url_path="bonds"),
    st.Page("views/method.py", title="Method", url_path="method"),
]
nav = st.navigation(pages, position="top")

# One quiet line under the navigation: data notice on the left, live/demo switch on the right.
as_of = max((data.meta.get("as_of") or {}).values(), default=None)
left, right = st.columns([8, 2], vertical_alignment="center")
with left:
    topline(as_of, data.fictional)
if len(available_modes()) > 1:
    with right, st.container(key="mode_switch"):
        choice = st.segmented_control(
            "Data",
            ["Live", "Demo"],
            default=current_mode().capitalize(),
            label_visibility="collapsed",
        )
    if choice and choice.lower() != current_mode():
        st.session_state["mode"] = choice.lower()
        st.query_params["data"] = choice.lower()
        st.rerun()

nav.run()
