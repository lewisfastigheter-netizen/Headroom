"""Source on hover: a figure with a data-sk (or data-src) attribute shows its source in a
small box.

The box is one fixed-position element on the page, placed next to the figure when the
pointer enters it, so it is never clipped by a scrolling table. A components v2 module
(no iframe) installs the listener once per page.

The same few source texts repeat on hundreds of figures (a municipality's area table has
over a thousand), so the views do not write the text into each figure. `src_attr(text)`
returns a short key (`data-sk="…"`) and remembers the text; `source_tips()`, called at the
end of the page, sends each text used in the run once, and the browser looks it up by key.
That cut the Locations page's HTML roughly in half. A literal `data-src="text"` still works
(ui.components.Stat uses it).
"""

from __future__ import annotations

import hashlib
import html

import streamlit as st
from streamlit.errors import StreamlitAPIException

_STATE = "_hr_src_tips"  # key -> text used since the last source_tips() call

JS = r"""
export default function (component) {
  const tips = (window.__hrTips = window.__hrTips || {});
  const data = component && component.data;
  if (data && data.tips) { Object.assign(tips, data.tips); }
  if (window.__hrSrcTips) return;
  window.__hrSrcTips = true;
  const box = document.createElement('div');
  box.className = 'hr-srctip';
  box.setAttribute('role', 'tooltip');
  document.body.appendChild(box);
  let cur = null;
  const hide = () => { box.style.display = 'none'; cur = null; };
  const textOf = (el) => el.getAttribute('data-src') || tips[el.getAttribute('data-sk')] || '';
  document.addEventListener('mouseover', (e) => {
    const el = e.target && e.target.closest ? e.target.closest('[data-src],[data-sk]') : null;
    if (el === cur) return;
    if (!el) { hide(); return; }
    const text = textOf(el);
    if (!text) { hide(); return; }
    cur = el;
    box.textContent = text;
    box.style.display = 'block';
    const r = el.getBoundingClientRect();
    const bw = box.offsetWidth, bh = box.offsetHeight;
    let left = r.left + r.width / 2 - bw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - bw - 8));
    let top = r.bottom + 8;
    if (top + bh > window.innerHeight - 8) top = Math.max(8, r.top - bh - 8);
    box.style.left = left + 'px';
    box.style.top = top + 'px';
  });
  window.addEventListener('scroll', hide, true);
}
"""


def _register():
    return st.components.v2.component("hr_source_tips", js=JS, isolate_styles=False)


_component = _register()


def src_key(text: str) -> str:
    """Stable short key for a source text (the same text gets the same key in every run)."""
    key = hashlib.blake2s(text.encode("utf-8"), digest_size=5).hexdigest()
    st.session_state.setdefault(_STATE, {})[key] = text
    return key


def src_attr(text: str) -> str:
    """' data-sk="…"' for an element whose hover shows `text`; '' for no text."""
    return f' data-sk="{src_key(text)}"' if text else ""


def src_span(inner: str, text: str, cls: str = "") -> str:
    """`inner` wrapped in a span that shows `text` on hover."""
    c = f' class="{html.escape(cls)}"' if cls else ""
    return f"<span{c}{src_attr(text)}>{inner}</span>"


def source_tips() -> None:
    """Mount once per page, at the end (after the figures); renders nothing visible."""
    tips = st.session_state.pop(_STATE, None) or {}
    with st.container(key="hr_source_tips"):
        try:
            _component(data={"tips": tips}, key="hr_source_tips_c")
        except StreamlitAPIException:  # a new runtime (tests, restart) with this module cached
            _register()(data={"tips": tips}, key="hr_source_tips_c")
