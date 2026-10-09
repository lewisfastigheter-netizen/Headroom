"""The large place search on the Locations page.

A small Streamlit component (components v2, no iframe), so the rows can carry two type
styles: the name large and the level and parent in --muted, styled from theme.css.
Matching runs in the browser over the place index (counties, municipalities, cities and
RegSO areas) with the same rules as headroom.model.search: a word prefix, a substring,
or one or two typos; counties and municipalities rank before cities, cities before areas.

The index is sent once per session and kept in the page (window) after that.
"""

from __future__ import annotations

import hashlib
import json

import streamlit as st
from streamlit.errors import StreamlitAPIException

CSS = ""  # styles live in theme.css (.hr-psearch), so they follow the app's tokens

JS = r"""
export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  const cache = (window.__hrPlaces = window.__hrPlaces || {});
  if (data && data.items) { cache[data.version] = data.items; }
  const items = (data && cache[data.version]) || [];

  let root = parentElement.querySelector('.hr-psearch');
  if (!root) {
    root = document.createElement('div');
    root.className = 'hr-psearch';
    root.innerHTML = '<input type="text" autocomplete="off" spellcheck="false" aria-label="Search places" />'
      + '<ul class="hr-psearch-list" role="listbox"></ul>';
    parentElement.appendChild(root);
  }
  const input = root.querySelector('input');
  const list = root.querySelector('ul');
  input.placeholder = (data && data.placeholder) || 'Search';

  const fold = (s) => (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  if (!cache['_folded_' + data.version]) {
    cache['_folded_' + data.version] = items.map((it) => it[4].map((k) => {
      const t = fold(k); return { t, w: t.split(' '), c: t.replace(/ /g, '') };
    }));
  }
  const folded = cache['_folded_' + data.version];

  function dist(a, b) {
    const d = [];
    for (let i = 0; i <= a.length; i++) { d.push([i]); }
    for (let j = 1; j <= b.length; j++) { d[0][j] = j; }
    for (let i = 1; i <= a.length; i++) {
      for (let j = 1; j <= b.length; j++) {
        const cost = a[i - 1] === b[j - 1] ? 0 : 1;
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
          d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
        }
      }
    }
    return d[a.length][b.length];
  }
  function score(q, qc, k, typo) {
    if (k.t.startsWith(q) || k.c.startsWith(qc)) return 0;
    if (k.w.some((w) => w.startsWith(q))) return 1;
    if (k.t.includes(q) || k.c.includes(qc)) return 2;
    if (!typo) return null;
    const limit = qc.length < 4 ? 0 : qc.length < 8 ? 1 : 2;
    if (!limit) return null;
    let best = null;
    for (const w of [k.c, ...k.w]) {
      if (!w || w[0] !== qc[0]) continue;
      for (let n = Math.max(1, qc.length - 1); n <= qc.length + 1 && n <= w.length; n++) {
        const dd = dist(qc, w.slice(0, n));
        if (dd <= limit && (best === null || dd < best)) best = dd;
      }
    }
    return best === null ? null : 3 + best;
  }
  function search(query) {
    const q = fold(query); const qc = q.replace(/ /g, '');
    if (!q) return [];
    let hits = [];
    for (let i = 0; i < items.length; i++) {
      let s = null;
      for (const k of folded[i]) { const v = score(q, qc, k, false); if (v !== null && (s === null || v < s)) s = v; }
      if (s !== null) hits.push([items[i][3], s, items[i][0].length, i]);
    }
    if (hits.length < 8) {
      const seen = new Set(hits.map((h) => h[3]));
      for (let i = 0; i < items.length; i++) {
        if (seen.has(i)) continue;
        let s = null;
        for (const k of folded[i]) { const v = score(q, qc, k, true); if (v !== null && (s === null || v < s)) s = v; }
        if (s !== null) hits.push([items[i][3], s, items[i][0].length, i]);
      }
    }
    hits.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]));
    return hits.slice(0, 8).map((h) => items[h[3]]);
  }

  let current = []; let active = -1;
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function render() {
    if (!current.length) { list.innerHTML = ''; root.classList.remove('open'); return; }
    list.innerHTML = current.map((it, i) =>
      '<li role="option" data-i="' + i + '" class="' + (i === active ? 'active' : '') + '">'
      + '<span class="n">' + esc(it[0]) + '</span><span class="s">' + esc(it[1]) + '</span></li>').join('');
    root.classList.add('open');
  }
  function pick(i) {
    const it = current[i]; if (!it) return;
    input.value = it[0]; current = []; render();
    setTriggerValue('pick', it[2]);
  }
  if (!root.dataset.bound) {
    root.dataset.bound = '1';
    input.addEventListener('input', () => { current = search(input.value); active = current.length ? 0 : -1; render(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { active = Math.min(active + 1, current.length - 1); render(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
      else if (e.key === 'Enter') { if (active >= 0) pick(active); e.preventDefault(); }
      else if (e.key === 'Escape') { current = []; render(); }
    });
    list.addEventListener('mousedown', (e) => {
      const li = e.target.closest('li'); if (li) { e.preventDefault(); pick(+li.dataset.i); }
    });
    input.addEventListener('blur', () => setTimeout(() => { current = []; render(); }, 120));
  }
}
"""


def _register():
    return st.components.v2.component("hr_place_search", css=CSS, js=JS, isolate_styles=False)


_component = _register()


def place_search(
    index: list[dict], key: str = "place_search", placeholder: str | None = None
) -> str | None:
    """Render the search box; returns the picked place value ('kommun:0380') or None."""
    payload = [[i["label"], i["sub"], i["value"], i["tier"], i["keys"]] for i in index]
    version = hashlib.sha1(
        json.dumps([len(payload), payload[:3], payload[-3:]]).encode()
    ).hexdigest()[:12]
    sent = st.session_state.setdefault("_hr_places_sent", set())
    data = {
        "version": version,
        "placeholder": placeholder or "Search a county, municipality, city or area",
    }
    if version not in sent:
        data["items"] = payload
        sent.add(version)
    with st.container(key=key):
        try:
            res = _component(data=data, key=f"{key}_c", on_pick_change=lambda: None)
        except StreamlitAPIException:  # a new runtime (tests, restart) with this module cached
            res = _register()(data=data, key=f"{key}_c", on_pick_change=lambda: None)
    return getattr(res, "pick", None)
