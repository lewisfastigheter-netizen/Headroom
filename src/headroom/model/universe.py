"""Which bond issuers are Swedish property companies.

Until Bolagsverket SNI codes are connected (milestone 4), an issuer counts as a
property company if its GLEIF legal name contains a property keyword, or matches
a seed name in config/universe_seed.yaml. Every company records which rule
admitted it, so the heuristic is visible and can be audited.
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from headroom.config import CONFIG_DIR

KEYWORDS = re.compile(
    r"fastighet|bostad|bostäder|hyresfastig|samhällsbygg|\bproperty\b|\bproperties\b|real estate",
    re.IGNORECASE,
)


@lru_cache
def seed() -> dict:
    with open(CONFIG_DIR / "universe_seed.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def classify(legal_name: str, lei: str | None = None) -> str | None:
    """Return the rule that admits this issuer, or None if it is not a property company."""
    s = seed()
    if lei and lei in (s.get("exclude_leis") or []):
        return None
    if lei and lei in (s.get("include_leis") or []):
        return "manual include"
    name = legal_name.lower()
    if any(w in name for w in (s.get("exclude_words") or [])):
        return None
    if KEYWORDS.search(legal_name):
        return "name keyword"
    for seed_name in s.get("property_names") or []:
        if re.search(rf"(?<!\w){re.escape(seed_name.lower())}(?!\w)", name):
            return "seed list"
    return None
