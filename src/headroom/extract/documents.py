"""PDF documents: download once, hash, read page text, pick the pages that matter.

Reports run to 30-60 pages. Sending all of them to an LLM is slow and costly,
so pages are scored on finance keywords (English and Swedish) and only the best
are sent. Page numbers are kept so every extracted figure can cite its page.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from headroom.config import CACHE_DIR
from headroom.http import Fetcher

DOC_DIR = CACHE_DIR / "documents"

# (pattern, weight). Patterns are matched case-insensitively on page text.
KPI_TERMS: list[tuple[str, int]] = [
    (r"loan[- ]to[- ]value|belåningsgrad|\bLTV\b", 5),
    (r"interest coverage|räntetäckningsgrad|\bICR\b", 5),
    (r"average interest rate|genomsnittlig ränta|snittränta", 4),
    (r"fixed[- ]interest period|räntebindning|interest rate fixing|fixed[- ]rate period", 4),
    (r"debt maturit|loan maturit|kapitalbindning|förfallostruktur|maturity structure", 4),
    (r"equity ratio|soliditet", 3),
    (
        r"EPRA NRV|EPRA NTA|long-term net asset value|långsiktigt substansvärde|net asset value per share|substansvärde per aktie",
        4,
    ),
    (r"unutilised|unused credit|undrawn|outnyttjade|ej utnyttjade|outnyttjad", 3),
    (r"cash and cash equivalents|likvida medel", 2),
    (
        r"investment properties|förvaltningsfastigheter|fair value of properties|fastigheternas verkliga värde|property value|fastighetsvärde",
        3,
    ),
    (r"interest[- ]bearing liabilities|räntebärande skulder", 3),
    (r"net interest|finansnetto|räntekostnader|interest expense", 2),
    (r"key (figures|ratios)|nyckeltal", 2),
    (r"interest rate derivatives|räntederivat|interest rate swaps|ränteswappar|hedg|säkr", 2),
    (r"segment|region|property category|fastighetskategori|kategori", 1),
]
COMPILED = [(re.compile(p, re.IGNORECASE), w) for p, w in KPI_TERMS]


@dataclass
class Document:
    url: str
    path: Path
    sha256: str
    pages: list[str]  # page text, index 0 = page 1

    @property
    def n_pages(self) -> int:
        return len(self.pages)


def fetch_pdf(f: Fetcher, url: str) -> Document:
    name = hashlib.sha1(url.encode()).hexdigest()[:20] + ".pdf"
    path = f.download(url, DOC_DIR / name, check_robots=True)
    data = path.read_bytes()
    return Document(url, path, hashlib.sha256(data).hexdigest(), read_pages(path))


def read_pages(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [page.get_text("text", sort=True) for page in doc]


def score_page(text: str) -> int:
    return sum(w * min(len(rx.findall(text)), 3) for rx, w in COMPILED)


def select_pages(doc: Document, max_pages: int = 10, always_first: int = 2) -> list[int]:
    """1-based page numbers: the first pages (summary, key figures) plus the best-scoring ones."""
    scored = sorted(range(doc.n_pages), key=lambda i: score_page(doc.pages[i]), reverse=True)
    chosen = list(range(min(always_first, doc.n_pages)))
    for i in scored:
        if len(chosen) >= max_pages:
            break
        if i not in chosen and score_page(doc.pages[i]) > 0:
            chosen.append(i)
    return sorted(p + 1 for p in chosen)


def render_for_llm(doc: Document, pages: list[int], max_chars_per_page: int = 7000) -> str:
    parts = []
    for p in pages:
        text = re.sub(r"[ \t]+", " ", doc.pages[p - 1]).strip()
        parts.append(f'<page number="{p}">\n{text[:max_chars_per_page]}\n</page>')
    return "\n\n".join(parts)
