"""Thin LLM layer: one call shape for Anthropic and OpenAI, typed outputs, a disk cache.

Outputs are Pydantic models (structured outputs on both APIs). Results are
cached by a key built from the document hash, the prompt version and the
model, so re-running the pipeline never pays twice for the same document.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from headroom.config import CACHE_DIR, settings

log = logging.getLogger(__name__)
LLM_CACHE = CACHE_DIR / "llm"  # legacy: one JSON file per call
# One append-only JSON-lines file, committed to git so a fresh checkout (or the
# scheduled GitHub job) never pays twice for a document already read.
LLM_STORE = CACHE_DIR / "llm.jsonl"
_memo: dict[str, dict] | None = None


def _store() -> dict[str, dict]:
    global _memo
    if _memo is None:
        _memo = {}
        if LLM_STORE.exists():
            with LLM_STORE.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        rec = json.loads(line)
                        _memo[rec.pop("key")] = rec
    return _memo


def _lookup(h: str) -> dict | None:
    rec = _store().get(h)
    if rec is None and (LLM_CACHE / f"{h}.json").exists():  # migrate a legacy entry
        rec = json.loads((LLM_CACHE / f"{h}.json").read_text("utf-8"))
        _save(h, rec)
    return rec


def _save(h: str, rec: dict) -> None:
    _store()[h] = rec
    LLM_STORE.parent.mkdir(parents=True, exist_ok=True)
    with LLM_STORE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": h, **rec}, ensure_ascii=False) + "\n")


def migrate_legacy() -> int:
    """Fold the per-file cache into the JSON-lines store. Returns entries added."""
    added = 0
    for f in sorted(LLM_CACHE.glob("*.json")) if LLM_CACHE.exists() else []:
        if f.stem not in _store():
            _save(f.stem, json.loads(f.read_text("utf-8")))
            added += 1
    return added


T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """No API key configured for the selected provider."""


def cache_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:40]


def model_name(fast: bool = False) -> str:
    s = settings()
    if s.llm_provider == "openai":
        return s.openai_fast_model if fast else s.openai_model
    return s.anthropic_fast_model if fast else s.anthropic_model


def structured(
    output: type[T],
    system: str,
    user: str,
    *,
    key: str,
    fast: bool = False,
    max_tokens: int = 4000,
) -> tuple[T, dict]:
    """Return (parsed object, metadata). Cached on `key` + model."""
    s = settings()
    model = model_name(fast)
    h = cache_key(key, s.llm_provider, model)
    rec = _lookup(h)
    if rec is not None:
        return output.model_validate(rec["output"]), {**rec["meta"], "cached": True}
    if not s.llm_available:
        raise LLMUnavailable(
            f"Set {'OPENAI' if s.llm_provider == 'openai' else 'ANTHROPIC'}_API_KEY in .env"
        )

    if s.llm_provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        try:
            resp = client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            # A rejected key will not start working mid-run: stop calling the API.
            raise LLMUnavailable(f"Anthropic rejected the API key: {e}") from e
        if resp.stop_reason not in ("end_turn", "stop_sequence") or resp.parsed_output is None:
            raise RuntimeError(f"LLM stopped with {resp.stop_reason}")
        parsed = resp.parsed_output
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    else:
        import openai

        client = openai.OpenAI(api_key=s.openai_api_key)
        resp = client.responses.parse(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text_format=output,
            max_output_tokens=max_tokens,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise RuntimeError("LLM returned no parsed output")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}

    meta = {
        "provider": s.llm_provider,
        "model": model,
        "usage": usage,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _save(h, {"output": parsed.model_dump(mode="json"), "meta": meta})
    log.info("LLM %s: %s in / %s out tokens", model, usage["input_tokens"], usage["output_tokens"])
    return parsed, {**meta, "cached": False}
