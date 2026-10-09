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
    st.Page("views/locations.py", title="Locations", url_path="locations"),
    st.Page("views/method.py", title="Method", url_path="method"),
    # Company pages open from the tables; they are not a menu item.
    st.Page("views/issuer.py", title="Company", url_path="issuer", visibility="hidden"),
]
company_page = pages[-1]
companies_page = pages[1]
nav = st.navigation(pages, position="top")


# Search in the top-right corner: a company opens its page; a county or city opens the
# Companies list filtered to it. The list only appears once something is typed (CSS).
def _picked() -> None:
    st.session_state["goto"] = st.session_state.get("header_search_box")
    st.session_state["header_search_box"] = None


sc = data.scores
choices: dict[str, str] = {f"org:{o}": n for o, n in zip(sc["org_nr"], sc["name"], strict=True)}
for county in sorted(set(sc["county"].drop_nulls())):
    choices[f"county:{county}"] = f"{county} · county"
for city, county in sorted(set(zip(sc["city"], sc["county"], strict=True)) - {(None, None)}):
    if city:
        choices[f"city:{county}|{city}"] = f"{city} · city"
with st.container(key="header_search"):
    st.selectbox(
        "Search company or location",
        sorted(choices, key=lambda k: choices[k].lower()),
        index=None,
        placeholder="Search company or location",
        format_func=choices.get,
        label_visibility="collapsed",
        key="header_search_box",
        on_change=_picked,
    )
goto = st.session_state.pop("goto", None)
if goto:
    kind, _, value = goto.partition(":")
    if kind == "org":
        st.switch_page(company_page, query_params={"org": value})
    elif kind == "county":
        st.switch_page(companies_page, query_params={"county": value})
    else:
        county, _, city = value.partition("|")
        st.switch_page(companies_page, query_params={"county": county, "city": city})

nav.run()

# Data notice at the foot of every page.
as_of = max((data.meta.get("as_of") or {}).values(), default=None)
st.write("")
topline(as_of, data.fictional)
