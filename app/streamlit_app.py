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
from headroom.model.search import prepare, search_tiered  # noqa: E402
from ui.components import inject_css, topline  # noqa: E402
from ui.data import get_data  # noqa: E402
from ui.locdata import index_for_session  # noqa: E402

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
locations_page = pages[2]
nav = st.navigation(pages, position="top")


# Search in the top-right corner, over companies and places. A company opens its page.
# A county or municipality gives two suggestions: the place on Locations, and the
# Companies list filtered to it (when companies are there). Cities and RegSO areas open
# Locations. Companies, counties and municipalities rank before cities, cities before
# areas, at most eight suggestions; close matches only, allowing a typo or two
# (see headroom.model.search).
sc = data.scores
company_items = tuple((n, o) for o, n in zip(sc["org_nr"], sc["name"], strict=True))
company_places = tuple(
    sorted({(ci, co) for ci, co in zip(sc["city"], sc["county"], strict=True) if ci and co})
)
company_counties = tuple(sorted(set(sc["county"].drop_nulls())))


@st.cache_resource(show_spinner=False)
def header_entries(companies: tuple, places: tuple, counties: tuple, places_version: int):
    items: list[tuple[str, str, float, list[str]]] = [(n, f"org:{o}", 0, [n]) for n, o in companies]
    index = index_for_session()
    kommun_names = set()
    for p in index:
        kind = p["value"].partition(":")[0]
        name = p["label"]
        if kind == "lan":
            items.append((f"{name} · county", f"loc:{p['value']}", 1, p["keys"]))
            if name in counties:
                items.append((f"Companies in {name}", f"county:{name}", 1.1, p["keys"]))
        elif kind == "kommun":
            kommun_names.add(name)
            items.append((f"{name} · municipality", f"loc:{p['value']}", 1, p["keys"]))
            for city, county in places:
                if city == name:
                    items.append((f"Companies in {name}", f"city:{county}|{city}", 1.1, [name]))
        elif kind == "tatort":
            items.append((f"{name} · city", f"loc:{p['value']}", 2, p["keys"]))
        else:
            kommun = p["sub"].split(" · ", 1)[-1].removesuffix(" municipality")
            items.append((f"{name} · area, {kommun}", f"loc:{p['value']}", 3, p["keys"]))
    # company cities that are postal towns, not municipalities: keep today's behaviour
    for city, county in places:
        if city not in kommun_names:
            items.append((f"Companies in {city}", f"city:{county}|{city}", 2.1, [city]))
    if not index:  # no location snapshot: counties from the companies, as before
        items += [(f"{c} · county", f"county:{c}", 1, [c]) for c in counties]
    return prepare(items)


entries = header_entries(company_items, company_places, company_counties, len(index_for_session()))

with st.container(key="header_search"):
    goto = st_searchbox(
        lambda q: search_tiered(q, entries) if q and q.strip() else [],
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
    elif kind == "loc":
        level, _, code = value.partition(":")
        st.switch_page(locations_page, query_params={"level": level, "code": code})
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
