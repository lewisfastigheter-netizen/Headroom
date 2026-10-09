"""Which value a reported property figure is: market (fair) value or book value.

Swedish companies with securities on a regulated market report investment property
at fair value under IFRS (IAS 40). Private companies following K2/K3 carry property
at cost less depreciation, often far below market value; K3 companies frequently
disclose the fair value in a note. LTV on book value overstates leverage, so every
property value carries a basis label and the app never mixes the two silently.
"""

from __future__ import annotations

import re

FAIR = "fair_value"  # stated as fair/market value in the source
FAIR_PRESUMED = "fair_value_presumed"  # IFRS interim report, basis not stated on the page
BOOK = "book_value"  # cost less depreciation (K2/K3)

LABEL = {
    FAIR: "Market value",
    FAIR_PRESUMED: "Market value (IFRS)",
    BOOK: "Book value",
}
NOTE = {
    FAIR: "Stated in the source as fair value (verkligt värde / marknadsvärde).",
    FAIR_PRESUMED: (
        "Basis not stated next to the figure. Issuers reporting under IFRS value "
        "investment property at fair value (IAS 40)."
    ),
    BOOK: (
        "Book value under K2/K3: acquisition cost less depreciation, usually below market "
        "value. LTV on book value overstates leverage."
    ),
}

_FAIR = re.compile(
    r"verklig[at]? värde|verkliga värden|fair value|marknadsvärde|market value|ias 40", re.I
)
_BOOK = re.compile(
    r"bokfört värde|bokförda värde|anskaffningsvärde|acquisition cost|historical cost|"
    r"book value|cost less depreciation|\bk[23]\b",
    re.I,
)


def classify(text: str | None, default: str | None = FAIR_PRESUMED) -> str | None:
    """Basis from the wording around a property value. Ambiguous text gives the default."""
    t = text or ""
    fair, book = bool(_FAIR.search(t)), bool(_BOOK.search(t))
    if fair and not book:
        return FAIR
    if book and not fair:
        return BOOK
    return default
