"""Small HTML building blocks for the editorial layout.

Streamlit widgets handle interaction; these helpers handle typography and
structure where Streamlit's defaults cannot meet the style.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import streamlit as st

CSS_PATH = Path(__file__).with_name("theme.css")

esc = html.escape


def inject_css() -> None:
    st.html(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>")


def render(markup: str) -> None:
    st.html(markup)


# --------------------------------------------------------------------------- formatting


def fmt_date(d: date | datetime | str | None, style: str = "short") -> str:
    if d is None:
        return "–"
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    if style == "long":
        return f"{d.day} {d:%B %Y}"
    if style == "month":
        return f"{d:%b %Y}"
    return d.isoformat()


def fmt_sek_m(v: float | None, unit: bool = True) -> str:
    """SEK millions, bn above 10,000m."""
    if v is None:
        return "–"
    s = f"{v / 1000:,.1f}bn" if abs(v) >= 10_000 else f"{v:,.0f}m"
    return f"SEK {s}" if unit else s


def fmt_pct(v: float | None, digits: int = 0, signed: bool = False) -> str:
    if v is None:
        return "–"
    s = f"{v * 100:+.{digits}f}" if signed else f"{v * 100:.{digits}f}"
    return s.replace("-", "−") + "%"


def fmt_x(v: float | None, digits: int = 2) -> str:
    return "–" if v is None else f"{v:.{digits}f}x"


def fmt_num(v: float | None, digits: int = 0) -> str:
    return "–" if v is None else f"{v:,.{digits}f}".replace("-", "−")


def band(score: float | None, high: float, watch: float) -> str:
    if score is None:
        return "ok"
    return "high" if score >= high else "watch" if score >= watch else "ok"


def marker(state: str) -> str:
    return f'<span class="hr-mk {state}"></span>'


def flag(label: str, tip: str, kind: str = "watch") -> str:
    """Small label shown under a figure; hovering it explains what it means."""
    return f'<span class="hr-flag {kind}" data-tip="{esc(tip)}">{esc(label)}</span>'


def unverified(
    value_html: str, conf: float | None, min_conf: float, source: str | None = None
) -> str:
    if conf is None or conf >= min_conf:
        return value_html
    tip = (
        f"Read from the report with low confidence ({conf:.0%}). The figure may be misread "
        "or derived from an ambiguous table; check the source before relying on it."
    )
    if source:
        tip += f" Source: {source}."
    return value_html + flag("Unverified", tip)


def basis_tag(basis: str | None, short: bool = False) -> str:
    """Flag saying a property value (and the LTV on it) is book value, not market value.
    `short` (dense tables) shows only the book-value case; otherwise every basis is shown."""
    from headroom.model import valuation

    if not basis or (short and basis != valuation.BOOK):
        return ""
    label = "Book" if short else valuation.LABEL.get(basis, basis)
    kind = "high" if basis == valuation.BOOK else "neutral"
    return flag(label, valuation.NOTE.get(basis, ""), kind)


# --------------------------------------------------------------------------- layout blocks


def topline(as_of: str | None, fictional: bool) -> None:
    """Data notice at the foot of each page: sources (or demo notice) and data date."""
    left = (
        '<span class="demo">'
        + marker("watch")
        + "<span><b>Demo mode.</b> All companies, bonds, figures and events are fictional."
        "</span></span>"
        if fictional
        else "<span><b>Live data.</b> ESMA, GLEIF, Riksbank, Bolagsverket, MFN, Cision, "
        "company reports.</span>"
    )
    right = f'<span class="asof">Data as of {fmt_date(as_of, "long")}</span>' if as_of else ""
    render(f'<div class="hr-topline">{left}{right}</div>')


def eyebrow(text: str) -> str:
    return f'<div class="hr-eyebrow">{esc(text)}</div>'


@dataclass
class Stat:
    value: str
    unit: str = ""
    caption: str = ""  # may contain <b>
    ink: bool = False
    state: str | None = None  # optional status marker


def stats_html(items: Sequence[Stat]) -> str:
    out = []
    for s in items:
        unit = f"<small>{esc(s.unit)}</small>" if s.unit else ""
        mk = marker(s.state) if s.state else ""
        cls = "v ink" if s.ink else "v"
        out.append(
            f'<div class="hr-stat"><div class="{cls}">{mk}{esc(s.value)}{unit}</div>'
            f'<div class="c">{s.caption}</div></div>'
        )
    return f'<div class="hr-stats">{"".join(out)}</div>'


def hero(
    kicker: str, headline: str, intro: str = "", stats: Sequence[Stat] = (), extra_left: str = ""
) -> None:
    """EQT-style opening: statement on the left, stacked figures on the right."""
    intro_html = f'<p class="intro">{intro}</p>' if intro else ""
    right = stats_html(stats) if stats else ""
    render(
        f'<div class="hr-hero"><div>{eyebrow(kicker)}<h1>{headline}</h1>{intro_html}'
        f"{extra_left}</div><div>{right}</div></div>"
    )


def section(n: int | str | None, title: str, desc: str = "") -> None:
    """Section heading. `n` is accepted for old call sites but no longer shown."""
    d = f'<p class="d">{desc}</p>' if desc else ""
    render(f'<div class="hr-section"><span class="t">{esc(title)}</span>{d}</div>')


def news(items: Sequence[dict]) -> None:
    """items: date, kind, title, company_html, source_html, state"""
    arts = []
    for it in items:
        arts.append(
            f'<article><div class="meta"><span class="date">{fmt_date(it["date"])}</span>'
            f'<span class="kind">{marker(it.get("state", "ok"))}{esc(it["kind"])}</span></div>'
            f'<h3>{esc(it["title"])}</h3><div class="co">{it["company_html"]}</div>'
            f'<div class="src">{it["source_html"]}</div></article>'
        )
    render(f'<div class="hr-news">{"".join(arts)}</div>')


def facts(items: Sequence[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div><div class="k">{esc(k)}</div><div class="v">{v}</div></div>' for k, v in items
    )
    render(f'<div class="hr-facts">{cells}</div>')


def source_line(text: str) -> None:
    render(f'<div class="hr-source">{text}</div>')


def note(text: str) -> None:
    render(f'<div class="hr-note">{text}</div>')


def bar(share: float | None, blue: bool = False) -> str:
    w = 0 if share is None else max(0.0, min(1.0, share)) * 100
    cls = "hr-bar blue" if blue else "hr-bar"
    return f'<div class="{cls}"><i style="width:{w:.1f}%"></i></div>'


@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"  # text | num | mono | date | html | dim
    fmt: Callable[[Any], str] | None = None
    raw_html: bool = False


def table(
    rows: Iterable[dict[str, Any]],
    cols: Sequence[Col],
    foot: dict[str, str] | None = None,
    max_height: int | None = None,
    bleed: bool = False,
) -> None:
    """`bleed` runs the blue header row out to both edges of the window."""
    head = "".join(
        f'<th class="{"num" if c.kind == "num" else ""}">{esc(c.label)}</th>' for c in cols
    )
    body = []
    for r in rows:
        tds = []
        for c in cols:
            v = r.get(c.key)
            s = c.fmt(v) if c.fmt else ("–" if v is None else str(v))
            if not (c.raw_html or c.kind == "html"):
                s = esc(s)
            tds.append(f'<td class="{c.kind}">{s}</td>')
        body.append(f"<tr>{''.join(tds)}</tr>")
    tfoot = ""
    if foot:
        tfoot = (
            "<tfoot><tr>"
            + "".join(f'<td class="{c.kind}">{foot.get(c.key, "")}</td>' for c in cols)
            + "</tr></tfoot>"
        )
    style = f' style="max-height:{max_height}px;overflow-y:auto"' if max_height else ""
    wrap = "hr-table-wrap bleed" if bleed else "hr-table-wrap"
    render(
        f'<div class="{wrap}"{style}><table class="hr-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody>{tfoot}</table></div>"
    )


def source_link(url: str | None, label: str | None = None) -> str:
    if not url:
        return "–"
    text = label or url
    if url.startswith("demo://"):
        return f'<span title="Fictional demo source">{esc(text)}</span>'
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(text)}</a>'
