"""Company and location search: close matches only, with room for typos.

A result must match the query as a word prefix, a substring, or within a small edit
distance of a word prefix (one typo for 4-7 letters, two from 8). So "saga" finds
Sagax but not Samhällsbyggnadsbolaget, and "sagga" still finds Sagax.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def fold(s: str | None) -> str:
    """Lower case, accents removed: 'Diös' -> 'dios'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _distance(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (optimal string alignment)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _allowed(n: int) -> int:
    return 0 if n < 4 else 1 if n < 8 else 2


def score(query: str, text: str) -> float | None:
    """Lower is better; None means no match."""
    return _score(fold(query), fold(text))


def _score(q: str, t: str, typo: bool = True) -> float | None:
    """score() on strings that are already folded."""
    if not q or not t:
        return None
    words = t.split()
    compact = t.replace(" ", "")
    qc = q.replace(" ", "")
    if t.startswith(q) or compact.startswith(qc):
        return 0.0
    if any(w.startswith(q) for w in words):
        return 1.0
    if q in t or qc in compact:
        return 2.0
    # typo tolerance: compare with word prefixes of about the same length
    limit = _allowed(len(qc))
    if not limit or not typo:
        return None
    best = None
    for w in [compact, *words]:
        for n in range(max(1, len(qc) - 1), len(qc) + 2):
            if n > len(w):
                break
            dist = _distance(qc, w[:n])
            if dist <= limit and (best is None or dist < best):
                best = dist
    return 3.0 + best if best is not None else None


def search(query: str, items: list[tuple[str, str]], limit: int = 8) -> list[tuple[str, str]]:
    """items: (label, value). Returns the closest matches, best first."""
    hits = []
    for label, value in items:
        s = score(query, label)
        if s is not None:
            hits.append((s, len(label), label, value))
    hits.sort()
    return [(label, value) for _, _, label, value in hits[:limit]]


# --------------------------------------------------------------------------- tiered index
# Thousands of places (RegSO areas, cities) are searched on every keystroke in the header,
# so their match strings are folded once, the typo pass only runs when exact matches run
# short, and a lower tier (companies, counties, municipalities) always ranks first.


@dataclass(frozen=True)
class Entry:
    label: str
    value: str
    tier: float
    keys: tuple[str, ...]  # folded strings to match against


def prepare(items: list[tuple[str, str, float, list[str]]]) -> list[Entry]:
    """items: (label, value, tier, match strings; the label when empty). Fold once and
    reuse on every keystroke."""
    return [
        Entry(lab, val, tier, tuple(dict.fromkeys(fold(k) for k in (keys or [lab]) if k)))
        for lab, val, tier, keys in items
    ]


def search_tiered(query: str, entries: list[Entry], limit: int = 8) -> list[tuple[str, str]]:
    """Best tier first, then closeness, then shorter label. Typos are tried only when the
    exact pass finds fewer than `limit` results, and only against keys with the same first
    letter (that keeps it fast over thousands of places)."""
    q = fold(query)
    if not q:
        return []
    hits: dict[int, tuple] = {}
    for i, e in enumerate(entries):
        best = None
        for k in e.keys:
            s = _score(q, k, typo=False)
            if s is not None and (best is None or s < best):
                best = s
        if best is not None:
            hits[i] = (e.tier, best, len(e.label), i)
    if len(hits) < limit and len(q.replace(" ", "")) >= 4:
        for i, e in enumerate(entries):
            if i in hits:
                continue
            best = None
            for k in e.keys:
                if not k or k[0] != q[0]:
                    continue
                s = _score(q, k)
                if s is not None and (best is None or s < best):
                    best = s
            if best is not None:
                hits[i] = (e.tier, best, len(e.label), i)
    order = sorted(hits.values())[:limit]
    return [(entries[i].label, entries[i].value) for *_, i in order]
