"""Plotly template: plum-black and plum on warm paper, no gridlines, a single baseline.

The two series colours separate well for colour-blind readers (deltaE above 30)
and both clear 3:1 contrast on white. Multi-series charts still carry a legend
and direct labels, so colour is never the only cue.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

PAPER = "#FCFAF7"
INK = "#1F1A21"
GRAPHITE = "#625A66"
MUTED = "#9C949F"
RULE = "#E4DED6"
TINT = "#F3EFE9"
ACCENT = "#7B3B6E"  # plum
ACCENT_LIGHT = "#D3B0C6"  # second series next to ACCENT (lighter tint of the same hue)
HIGH = "#D9571F"
WATCH = "#B8891C"

SANS = "Familjen Grotesk, Helvetica Neue, Arial, sans-serif"
DISPLAY = "Newsreader, Georgia, serif"
MONO = "IBM Plex Mono, Menlo, monospace"

CONFIG = {"displayModeBar": False, "responsive": True}

_axis = dict(
    showgrid=False,
    zeroline=False,
    showline=False,
    ticks="",
    tickfont=dict(family=SANS, size=11.5, color=GRAPHITE),
    title=dict(font=dict(family=MONO, size=10.5, color=GRAPHITE), standoff=10),
    automargin=True,
)

headroom = go.layout.Template(
    layout=go.Layout(
        font=dict(family=SANS, size=12.5, color=INK),
        paper_bgcolor=PAPER,
        plot_bgcolor=PAPER,
        colorway=[INK, ACCENT, MUTED, GRAPHITE],
        margin=dict(l=4, r=12, t=36, b=8),
        xaxis={**_axis, "showline": True, "linecolor": INK, "linewidth": 1},  # the baseline
        yaxis={
            **_axis,
            "showticklabels": True,
            "tickfont": dict(family=SANS, size=11, color=MUTED),
        },
        legend=dict(
            orientation="h",
            x=0,
            xanchor="left",
            y=1.02,
            yanchor="bottom",
            font=dict(family=SANS, size=12, color=GRAPHITE),
            bgcolor="rgba(0,0,0,0)",
            itemsizing="constant",
            traceorder="normal",
        ),
        hoverlabel=dict(bgcolor=PAPER, bordercolor=INK, font=dict(family=SANS, size=12, color=INK)),
        bargap=0.35,
        barmode="stack",
        hovermode="x unified",
    ),
    data=dict(
        bar=[go.Bar(marker=dict(line=dict(width=0)), textfont=dict(family=SANS, color=INK))],
        scatter=[go.Scatter(line=dict(width=1.75))],
    ),
)

pio.templates["headroom"] = headroom
pio.templates.default = "headroom"
