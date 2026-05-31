"""Extraction: turn a segment transcript into (a) an English-only Narrative
Primer and (b) exactly 15 lexical chunks (5 sentences / 5 noun-verb / 5 grammar),
extracted as-used and de-duplicated against what the learner already knows.

Built from scratch. The LLM provider is injected (OpenAIClient or FakeLLM) so the
whole pipeline — prompt building, JSON validation, the rewrite-on-failure loop,
dedupe and top-up — is testable offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config, db
from ..llm.client import LLMError, LLMProvider
from ..models import Card, ExtractionRun
from . import analyzer, ingest, srs

CHUNK_TYPES = config.CHUNK_TYPES                # ("sentence","noun_verb","grammar")
PER_TYPE = config.CHUNKS_PER_TYPE              # 5
_HANGUL = re.compile(r"[가-힣]")


@dataclass
class Chunk:
    type: str
    ko: str
    en: str
    gloss: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "ko": self.ko, "en": self.en, "gloss": self.gloss}


@dataclass
class ExtractionResult:
    primer_md: str
    chunks: list[Chunk]
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {t: sum(1 for c in self.chunks if c.type == t) for t in CHUNK_TYPES}


SYSTEM_PROMPT = (
    "You are a Korean tutor preparing a learner to watch a ~10-minute cartoon "
    "segment. You receive the Korean transcript of that segment.\n"
    "Return ONLY a JSON object with exactly these keys:\n"
    '- "primer_md": a scene-by-scene English summary (markdown), 1-3 sentences '
    "per scene. ENGLISH ONLY — never include Korean text or translations. "
    "Explicitly call out non-literal idioms, abstract bets/arguments, and shifts "
    "in tone or formality. It is a mental blueprint of the plot.\n"
    '- "chunks": an array of EXACTLY 15 lexical chunks extracted AS THEY APPEAR '
    "in the dialogue (NOT dictionary form), split as:\n"
    f"    * {PER_TYPE} with type \"sentence\": full, high-frequency sentences useful in daily conversation.\n"
    f"    * {PER_TYPE} with type \"noun_verb\": a common noun attached to its natural verb (a collocation).\n"
    f"    * {PER_TYPE} with type \"grammar\": a verb phrase highlighting ONE specific grammatical ending or particle used in the segment.\n"
    'Each chunk object has keys {"type","ko","en","gloss"}: ko = Korean exactly as '
    "used, en = natural English, gloss = a brief note (for grammar, name the "
    "ending/particle).\n"
    "Avoid chunks the learner already knows (a list may be provided); do not "
    "produce near-duplicates of known items.\n"
    "Output strictly valid JSON with no markdown fences."
)


def build_user_prompt(
    transcript: str, avoid_forms: list[str], *, extra: str = "",
) -> str:
    avoid = ", ".join(sorted(set(avoid_forms))[:400]) or "(none yet)"
    parts = [
        f"Already-known items to AVOID re-teaching: {{{avoid}}}",
        "",
        "Korean transcript of the segment:",
        transcript.strip(),
    ]
    if extra:
        parts += ["", extra]
    return "\n".join(parts)


# --- parsing / validation ------------------------------------------------

def parse_result(data: dict[str, Any]) -> ExtractionResult:
    if not isinstance(data, dict):
        raise LLMError("extraction response is not a JSON object")
    primer = data.get("primer_md")
    if not isinstance(primer, str) or not primer.strip():
        raise LLMError("missing/empty primer_md")
    raw_chunks = data.get("chunks")
    if not isinstance(raw_chunks, list):
        raise LLMError("missing chunks array")
    chunks: list[Chunk] = []
    for i, rc in enumerate(raw_chunks):
        if not isinstance(rc, dict):
            raise LLMError(f"chunk {i} is not an object")
        ctype = rc.get("type")
        ko = rc.get("ko")
        en = rc.get("en", "")
        gloss = rc.get("gloss", "")
        if ctype not in CHUNK_TYPES:
            raise LLMError(f"chunk {i} has bad type {ctype!r}")
        if not isinstance(ko, str) or not ko.strip():
            raise LLMError(f"chunk {i} has empty ko")
        chunks.append(Chunk(type=ctype, ko=ko.strip(),
                            en=str(en).strip(), gloss=str(gloss).strip()))
    warnings: list[str] = []
    if _HANGUL.search(primer):
        warnings.append("primer_md contains Korean text (should be English only)")
    return ExtractionResult(primer_md=primer.strip(), chunks=chunks, raw=data,
                            warnings=warnings)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).strip().lower()


def dedupe_chunks(chunks: list[Chunk], known: set[str]) -> list[Chunk]:
    """Keep chunks that introduce at least one NEW content lemma versus `known`
    (a set of surface forms), and drop exact-lemma duplicates among the chunks.
    Lemma-based, so 가요/갔어요/가다 are recognised as the same known word."""
    known_lemmas = analyzer.content_lemmas_of_many(known)
    known_norm = {_norm(k) for k in known}
    seen_sigs: set[Any] = set()
    out: list[Chunk] = []
    for c in chunks:
        clem = analyzer.lemma_set(c.ko)
        if clem:
            if not (clem - known_lemmas):          # introduces no new content word
                continue
            sig: Any = (c.type, tuple(sorted(clem)))
        else:                                       # no content lemmas detected
            n = _norm(c.ko)
            if n in known_norm:
                continue
            sig = (c.type, n)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        out.append(c)
    return out


def _short_types(chunks: list[Chunk]) -> dict[str, int]:
    have = {t: sum(1 for c in chunks if c.type == t) for t in CHUNK_TYPES}
    return {t: PER_TYPE - n for t, n in have.items() if n < PER_TYPE}


# --- orchestration -------------------------------------------------------

def extract_text(
    transcript: str,
    provider: LLMProvider,
    *,
    known_forms: Optional[set[str]] = None,
    model: Optional[str] = None,
    max_rewrites: int = 2,
    max_topups: int = 1,
) -> ExtractionResult:
    """Full extraction pipeline over raw transcript text (no DB)."""
    known = known_forms or set()
    model = model or config.EXTRACTION_MODEL

    result: ExtractionResult | None = None
    extra = ""
    for attempt in range(max_rewrites + 1):
        data = provider.complete_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(transcript, list(known), extra=extra),
            model=model, max_tokens=2048,
        )
        try:
            result = parse_result(data)
        except LLMError:
            if attempt >= max_rewrites:
                raise
            extra = "Your previous output was invalid JSON or had bad fields. Return EXACTLY the schema."
            continue
        counts = result.counts()
        if all(counts[t] == PER_TYPE for t in CHUNK_TYPES):
            break
        if attempt >= max_rewrites:
            result.warnings.append(f"counts off after rewrites: {counts}")
            break
        extra = (
            f"Your chunk counts were {counts}. Return EXACTLY {PER_TYPE} of each "
            f"type: {', '.join(CHUNK_TYPES)} (15 total)."
        )

    assert result is not None
    # dedupe against known deck, then top up any thinned categories
    result.chunks = dedupe_chunks(result.chunks, known)
    for _ in range(max_topups):
        short = _short_types(result.chunks)
        if not short:
            break
        selected = {c.ko for c in result.chunks}
        topup_extra = (
            "Provide ONLY additional chunks for these types and counts: "
            + ", ".join(f"{t} x{n}" for t, n in short.items())
            + ". Avoid all previously known items AND these already-chosen: {"
            + ", ".join(c.ko for c in result.chunks) + "}."
        )
        data = provider.complete_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(transcript, list(known | selected), extra=topup_extra),
            model=model, max_tokens=1024,
        )
        try:
            more = parse_result(data).chunks
        except LLMError:
            break
        more = dedupe_chunks(more, known | selected)
        for t, need in short.items():
            for c in [c for c in more if c.type == t][:need]:
                result.chunks.append(c)

    # trim to exactly PER_TYPE per type where possible, keep type order
    trimmed: list[Chunk] = []
    for t in CHUNK_TYPES:
        trimmed.extend([c for c in result.chunks if c.type == t][:PER_TYPE])
    result.chunks = trimmed
    final = result.counts()
    if any(final[t] != PER_TYPE for t in CHUNK_TYPES):
        result.warnings.append(f"final counts off: {final} (total {len(result.chunks)})")
    return result


def extract_segment(
    segment_id: int, provider: LLMProvider, *, model: Optional[str] = None,
    deck_name: str = "Korean", **kw,
) -> ExtractionResult:
    """Extract for a stored segment: pulls transcript + known deck vocab, runs the
    pipeline, and records an ExtractionRun for audit."""
    text = ingest.segment_text(segment_id)
    if not text:
        raise ValueError(f"segment {segment_id} has no transcript text")
    known = srs.known_surface_forms(deck_name=deck_name)
    result = extract_text(text, provider, known_forms=known, model=model, **kw)
    with db.session_scope() as s:
        s.add(ExtractionRun(
            segment_id=segment_id, primer_md=result.primer_md,
            raw_json=json.dumps(result.raw, ensure_ascii=False),
        ))
    return result


def accept_chunks(
    chunks: list[Chunk], *, segment_id: Optional[int] = None,
    deck_name: str = "Korean", today_ord: Optional[int] = None,
) -> list[Card]:
    """Turn accepted chunks into new cards, due the NEXT day (tomorrow's warm-up)."""
    today = today_ord if today_ord is not None else srs.today_ordinal()
    cards: list[Card] = []
    for c in chunks:
        cards.append(srs.add_card(
            front_ko=c.ko, back_en=c.en, gloss=c.gloss, kind=c.type,
            surface_forms=[c.ko], source_segment_id=segment_id,
            deck_name=deck_name, state="new", due_ord=today + 1,
        ))
    return cards
