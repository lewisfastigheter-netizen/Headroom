"""Source on hover: any element with a data-src attribute shows that text in a small box.

The box is one fixed-position element on the page, placed next to the figure when the
pointer enters it, so it is never clipped by a scrolling table. A components v2 module
(no iframe) installs the listener once per page; the text itself is plain HTML attributes
written by the views (see views/locations.py, tip()).
"""

from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitAPIException

JS = r"""
export default function () {
  if (window.__hrSrcTips) return;
  window.__hrSrcTips = true;
  const box = document.createElement('div');
  box.className = 'hr-srctip';
  box.setAttribute('role', 'tooltip');
  document.body.appendChild(box);
  let cur = null;
  const hide = () => { box.style.display = 'none'; cur = null; };
  document.addEventListener('mouseover', (e) => {
    const el = e.target && e.target.closest ? e.target.closest('[data-src]') : null;
    if (el === cur) return;
    if (!el) { hide(); return; }
    cur = el;
    box.textContent = el.getAttribute('data-src');
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


def source_tips() -> None:
    """Mount once per page, anywhere; renders nothing visible."""
    with st.container(key="hr_source_tips"):
        try:
            _component(key="hr_source_tips_c")
        except StreamlitAPIException:  # a new runtime (tests, restart) with this module cached
            _register()(key="hr_source_tips_c")
