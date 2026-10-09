"""Company and location search: close matches only, with room for typos.

A result must match the query as a word prefix, a substring, or within a small edit
distance of a word prefix (one typo for 4-7 letters, two from 8). So "saga" finds
Sagax but not Samhällsbyggnadsbolaget, and "sagga" still finds Sagax.
"""

from __future__ import annotations

import re
import unicodedata


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
    q, t = fold(query), fold(text)
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
    if not limit:
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
