"""Credit events from press releases and bondholder notices.

Two steps keep this cheap:
1. A keyword filter on the release title picks candidates (Swedish and English).
2. Only candidates are read in full and classified by the fast LLM model into
   the event taxonomy, with severity and a one-line analyst headline.
Routine interim reports are tagged by title alone, without the LLM.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from headroom.extract.llm import structured
from headroom.sources import newsfeeds
from headroom.sources.newsfeeds import Release

PROMPT_VERSION = "events-v1"

CANDIDATE = re.compile(
    r"skriftlig[t]? förfarande|written procedure|waiver|dispens|eftergift|standstill|"
    r"ränteuppskov|uppskjut|defer|PIK|rekonstruktion|reconstruction|konkurs|bankrupt|"
    r"likvidation|liquidat|going concern|fortsatt drift|rating|kreditbetyg|downgrad|"
    r"avyttr|divest|försälj|sells?\b|sale of|disposal|förvärv|refinans|refinanc|"
    r"återköp|repurchase|buy-?back|inlösen|redemption|tender offer|bondholder|"
    r"obligationsinnehav|covenant|villkorsändring|amendment|nyemission|rights issue|"
    r"directed issue|riktad|vinstvarning|profit warning|nedskrivning|impairment|"
    r"emitterar|issues? (?:new )?(?:senior )?(?:secured )?bonds?|obligationslån",
    re.IGNORECASE,
)

EventKind = Literal[
    "written_procedure",
    "waiver",
    "interest_deferral",
    "reconstruction",
    "bankruptcy_in_group",
    "liquidation",
    "going_concern",
    "disposal_below_book",
    "disposal",
    "rating_downgrade",
    "refinancing_completed",
    "equity_raise",
    "none",
]


class Classified(BaseModel):
    event_type: EventKind = Field(description="'none' if not a credit-relevant event")
    severity: int = Field(description="1 low, 2 medium, 3 high distress signal")
    headline: str = Field(description="One factual line, max 140 chars, no adjectives")


SYSTEM = """You classify Swedish property companies' press releases for a credit monitor.
Event types:
- written_procedure: issuer asks bondholders to amend bond terms (also results of such procedures)
- waiver: lenders or bondholders waive a covenant or test
- interest_deferral: interest payment deferred, PIK, or standstill on interest
- reconstruction: company reconstruction (företagsrekonstruktion) in the group
- bankruptcy_in_group: bankruptcy of the company or a subsidiary
- liquidation: liquidation of the company or a group company
- going_concern: auditor or board flags material uncertainty about going concern
- disposal_below_book: property or company sold below book/market value
- disposal: property sale at or above book value, or value not stated
- rating_downgrade: credit rating lowered or outlook cut to negative (not affirmations)
- refinancing_completed: new bond issued, loans refinanced, bonds repurchased or redeemed early
- equity_raise: share issue or capital injection
- none: anything else (acquisitions, appointments, interim reports, invitations)
Severity: 3 for written procedure on amendments/extensions, deferral, reconstruction,
bankruptcy, liquidation, going concern; 2 for waiver, downgrade, disposal below book;
1 otherwise. Headline: what happened, in English, with amounts if given."""


def is_candidate(title: str) -> bool:
    return bool(CANDIDATE.search(title)) and not newsfeeds.is_period_report(title)


def classify(rel: Release) -> Classified:
    text = f"Title: {rel.title}\nDate: {rel.published}\n\n{rel.body[:4000]}"
    out, _ = structured(
        Classified, SYSTEM, text, key=f"{PROMPT_VERSION}|{rel.url}", fast=True, max_tokens=1500
    )
    return out


def to_event(rel: Release, c: Classified) -> dict | None:
    if c.event_type == "none" or rel.published is None:
        return None
    # guard against the model calling an upgrade or affirmation a downgrade
    if (
        c.event_type == "rating_downgrade"
        and re.search(
            r"upgrad|affirm|höj|bekräft|positive outlook|positiva utsikter",
            f"{c.headline} {rel.title}",
            re.I,
        )
        and not re.search(
            r"downgrad|sänk|negative outlook|negativa utsikter", f"{c.headline} {rel.title}", re.I
        )
    ):
        return None
    return {
        "org_nr": rel.org_nr,
        "date": rel.published.date(),
        "type": c.event_type,
        "severity": max(1, min(3, c.severity)),
        "title": c.headline or rel.title,
        "source_url": rel.url,
        "source_name": "MFN" if rel.provider == "mfn" else "Cision",
    }


def report_event(rel: Release) -> dict | None:
    if not newsfeeds.is_period_report(rel.title) or rel.published is None:
        return None
    return {
        "org_nr": rel.org_nr,
        "date": rel.published.date(),
        "type": "interim_report",
        "severity": 1,
        "title": rel.title,
        "source_url": rel.url,
        "source_name": "MFN" if rel.provider == "mfn" else "Cision",
    }


def dedupe_languages(events: list[dict]) -> list[dict]:
    """Swedish and English versions of one release become one event (same company, day, type)."""
    seen: dict[tuple, dict] = {}
    for e in sorted(events, key=lambda e: (not re.search(r"[a-z]", e["title"][:1] or "a"),)):
        k = (e["org_nr"], e["date"], e["type"])
        if k not in seen:
            seen[k] = e
    return list(seen.values())


def since(rels: list[Release], cutoff: datetime) -> list[Release]:
    return [r for r in rels if r.published is None or r.published.replace(tzinfo=None) >= cutoff]
