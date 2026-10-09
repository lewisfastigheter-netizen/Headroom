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

from streamlit_searchbox import st_searchbox  # noqa: E402

import ui.plotly_template  # noqa: E402,F401  registers the Plotly template
from headroom.model.search import search  # noqa: E402
from ui.components import inject_css, topline  # noqa: E402
from ui.data import get_data  # noqa: E402

inject_css()
st.logo(str(Path(__file__).parent / "static" / "wordmark.svg"), size="large")

data = get_data()

pages = [
    st.Page("views/market.py", title="Market Overview", url_path="market", default=True),
    st.Page("views/companies.py", title="Companies", url_path="companies"),
    st.Page("views/locations.py", title="Locations", url_path="locations"),
    st.Page("views/method.py", title="Method", url_path="method"),
    # Company pages open from the tables; they are not a menu item.
    st.Page("views/issuer.py", title="Company", url_path="issuer", visibility="hidden"),
]
company_page = pages[-1]
SEARCH_STYLE = {
    "searchbox": {
        "control": {
            "backgroundColor": "#ffffff",
            "border": "none",
            "borderRadius": 2,
            "boxShadow": "none",
            "minHeight": "36px",
            "fontFamily": "Roboto, Helvetica Neue, Arial, sans-serif",
            "fontSize": "14px",
        },
        "input": {"color": "#212121"},
        "placeholder": {"color": "#6A6A6A"},
        "singleValue": {"color": "#212121"},
        "option": {
            "color": "#212121",
            "backgroundColor": "#ffffff",
            "highlightColor": "#E6F3F5",
            "fontFamily": "Roboto, Helvetica Neue, Arial, sans-serif",
            "fontSize": "14px",
        },
    },
    "dropdown": {"rotate": False, "width": 0, "height": 0, "fill": "transparent"},
    "clear": {
        "width": 12,
        "height": 12,
        "stroke": "#6A6A6A",
        "icon": "cross",
        "clearable": "always",
    },
}
companies_page = pages[1]
nav = st.navigation(pages, position="top")


# Search in the top-right corner: a company opens its page; a county or city opens the
# Companies list filtered to it. Suggestions appear while typing and only show close
# matches, allowing a typo or two (see headroom.model.search).
sc = data.scores
choices: list[tuple[str, str]] = [
    (n, f"org:{o}") for o, n in zip(sc["org_nr"], sc["name"], strict=True)
]
choices += [(f"{c} · county", f"county:{c}") for c in sorted(set(sc["county"].drop_nulls()))]
choices += [
    (f"{city} · city", f"city:{county}|{city}")
    for city, county in sorted(
        {(ci, co) for ci, co in zip(sc["city"], sc["county"], strict=True) if ci and co}
    )
]

with st.container(key="header_search"):
    goto = st_searchbox(
        lambda q: search(q, choices) if q and q.strip() else [],
        placeholder="Search company or location",
        key="header_search_box",
        clear_on_submit=True,
        debounce=120,
        style_absolute=True,
        style_overrides=SEARCH_STYLE,
    )
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
