"""Draft deal memo: an LLM writes from a numbered fact sheet and must cite it.

The fact sheet is built in code from the snapshot. Each fact has an id [S1],
[S2] and a source URL. The model may only use those facts; every sentence must
carry at least one citation. Sentences without a valid citation are flagged in
the output instead of being silently kept.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from headroom.extract.llm import structured

PROMPT_VERSION = "memo-v1"


@dataclass
class Fact:
    id: str
    text: str
    source: str


class Section(BaseModel):
    heading: str
    text: str = Field(description="Prose; every sentence ends with citations like [S3] or [S1][S4]")


class Memo(BaseModel):
    title: str
    sections: list[Section]


SYSTEM = """You are an associate at a Nordic value-add real estate investor writing a first-look memo
on a possible motivated seller. Use ONLY the numbered facts provided. Every sentence must end with
one or more citations in the form [S1]. Do not invent figures, names or events. Where a fact is
missing, say what should be checked. Plain analyst English, no hype, no exclamation marks.
Sections, in order: Situation; Why now; Refinancing and covenants; Possible transaction;
Risks and open questions; Next steps. Keep it under 450 words."""


def build_memo(company: str, facts: list[Fact]) -> tuple[Memo, dict]:
    sheet = "\n".join(f"[{f.id}] {f.text} (source: {f.source})" for f in facts)
    digest = hashlib.sha256(sheet.encode()).hexdigest()[:24]
    return structured(
        Memo,
        SYSTEM,
        f"Company: {company}\n\nFacts:\n{sheet}",
        key=f"{PROMPT_VERSION}|{digest}",
        max_tokens=3000,
    )


CITE = re.compile(r"\[S(\d+)\]")


def check_citations(memo: Memo, facts: list[Fact]) -> list[str]:
    """Return sentences that carry no valid citation."""
    valid = {f.id for f in facts}
    bad = []
    for s in memo.sections:
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", s.text.strip()):
            ids = {f"S{n}" for n in CITE.findall(sentence)}
            if sentence and (not ids or not ids <= valid):
                bad.append(sentence)
    return bad


def to_markdown(memo: Memo, facts: list[Fact], bad: list[str]) -> str:
    out = [
        f"# {memo.title}",
        "",
        "*Draft. Generated from the sources listed below; verify before use.*",
        "",
    ]
    for s in memo.sections:
        out += [f"## {s.heading}", "", s.text, ""]
    if bad:
        out += ["## Unsupported sentences", "", *[f"- {b}" for b in bad], ""]
    out += ["## Sources", ""]
    used = set(CITE.findall(json.dumps(memo.model_dump())))
    out += [f"- [S{f.id[1:]}] {f.text} ({f.source})" for f in facts if f.id[1:] in used]
    return "\n".join(out)
