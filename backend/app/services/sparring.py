"""Sparring: a deck-constrained Korean conversation partner.

Built from scratch (the prior Anki gateway code is NOT used). The reply continues
the cartoon scene; the assistant must use at least one of today's target chunks
and otherwise stay within already-known vocab. Vocabulary checking uses the Kiwi
morphological analyzer (see analyzer.py) — POS tagging + lemmatization — so there
are NO hand-maintained particle/ending lists. The validator is advisory and
drives a rewrite loop. At session end we harvest a few new chunks for tomorrow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel import select

from .. import config, db
from ..llm.client import LLMError, LLMProvider
from ..models import SparSession, SparTurn
from . import analyzer
from .extraction import Chunk, dedupe_chunks


# Ultra-common words that should never be flagged as "new vocab" (greetings,
# very common adverbs/pronouns). Keeps the advisory validator from crying wolf,
# especially when the deck is still small.
COMMON_LEMMAS = frozenset({
    "안녕", "안녕하다", "오늘", "내일", "어제", "지금", "정말", "너무", "아주",
    "좋다", "네", "아니다", "감사하다", "고맙다", "우리", "저", "나", "그",
})


def analyze_reply(
    reply_ko: str, allowed_forms: set[str], targets: list[str],
    *, target_overlap: float = 0.6,
) -> tuple[list[str], list[str]]:
    """Return (targets_used, unexpected_surfaces), computed from the text via lemma
    matching so we never trust the model's self-report.

    A target chunk counts as "used" only when enough of ITS content lemmas appear
    in the reply: single/two-lemma targets (words, collocations) need all of them;
    longer targets (full sentences) need >= target_overlap of them. This prevents a
    whole sentence target from being falsely credited because of one shared common
    verb like 가다 (the over-matching bug a real conversation exposed)."""
    morphs = analyzer.analyze(reply_ko)
    reply_lemmas = {m.lemma for m in morphs if m.is_content}
    allowed_lemmas = analyzer.content_lemmas_of_many(allowed_forms) | COMMON_LEMMAS

    used: list[str] = []
    for t in targets:
        tlem = set(analyzer.content_lemmas(t))
        if not tlem:
            continue
        need = 1.0 if len(tlem) <= 2 else target_overlap
        if len(tlem & reply_lemmas) / len(tlem) >= need:
            used.append(t)

    unexpected = [m.surface for m in morphs
                  if m.is_content and m.lemma not in allowed_lemmas]
    return used, unexpected


@dataclass
class SparResponse:
    reply_ko: str
    glosses: dict[str, str] = field(default_factory=dict)
    micro_feedback_en: str = ""
    suggested_user_reply_ko: str = ""
    suggested_user_reply_en: str = ""
    targets_used: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


SYSTEM_PROMPT = (
    "You are a friendly Korean conversation partner continuing the scene of a "
    "cartoon the learner just watched. Speak ONLY in polite 해요체.\n"
    "Rules:\n"
    "- reply_ko: 1-2 short sentences ending with exactly ONE question.\n"
    "- Use at least ONE of today's TARGET items (listed in the user message).\n"
    "- For content words prefer the ALLOWED list; do NOT introduce new content "
    "words. Particles/function words are always fine. If the flow conflicts with "
    "the vocab rules, change the topic to obey the rules.\n"
    "Return ONLY a JSON object with keys: reply_ko (string), glosses (object: "
    "each content word in reply_ko -> short English gloss), micro_feedback_en "
    "(string: brief feedback on the learner's Korean, include a correction if "
    "needed), suggested_user_reply_ko (a short non-question reply the learner "
    "could say, obeying the vocab rules), suggested_user_reply_en (its English), "
    "targets_used (array of target strings you used). No markdown fences."
)


def build_user_prompt(
    *, scene: str, allowed: list[str], targets: list[str],
    history: list[dict[str, str]] | None, user_text: str, extra: str = "",
) -> str:
    hist = history or []
    lines = [
        f"Scene so far (English): {scene or '(start of conversation)'}",
        f"Today's TARGET items (use at least one): {{{', '.join(targets) or '(none)'}}}",
        f"ALLOWED content words: {{{', '.join(sorted(set(allowed))[:400]) or '(none)'}}}",
        "",
        "Recent turns:",
    ]
    for turn in hist[-6:]:
        lines.append(f"  {turn.get('role', '?')}: {turn.get('text', '')}")
    lines += ["", f"Learner just said (KO): {user_text.strip() or '(nothing yet — greet them)'}"]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def parse_spar(data: dict[str, Any]) -> SparResponse:
    if not isinstance(data, dict):
        raise LLMError("sparring response is not an object")
    reply = data.get("reply_ko")
    if not isinstance(reply, str) or not reply.strip():
        raise LLMError("empty reply_ko")
    glosses = data.get("glosses") if isinstance(data.get("glosses"), dict) else {}
    return SparResponse(
        reply_ko=reply.strip(),
        glosses={str(k): str(v) for k, v in glosses.items()},
        micro_feedback_en=str(data.get("micro_feedback_en", "")),
        suggested_user_reply_ko=str(data.get("suggested_user_reply_ko", "")),
        suggested_user_reply_en=str(data.get("suggested_user_reply_en", "")),
    )


def spar_turn(
    *, user_text: str, provider: LLMProvider, targets: list[str],
    support: list[str], scene: str = "", history: list[dict[str, str]] | None = None,
    model: Optional[str] = None, max_rewrites: int = 2,
) -> SparResponse:
    allowed = list(dict.fromkeys(list(targets) + list(support)))
    allowed_set = set(allowed)
    model = model or config.SPARRING_MODEL
    extra = ""
    resp: SparResponse | None = None
    for attempt in range(max_rewrites + 1):
        data = provider.complete_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(scene=scene, allowed=allowed, targets=targets,
                                   history=history, user_text=user_text, extra=extra),
            model=model, max_tokens=600,
        )
        try:
            resp = parse_spar(data)
        except LLMError:
            if attempt >= max_rewrites:
                raise
            extra = "Return ONLY a valid JSON object matching the schema."
            continue
        used, unexpected = analyze_reply(resp.reply_ko, allowed_set, targets)
        resp.targets_used = used
        resp.unexpected = unexpected
        if (used and not unexpected) or attempt >= max_rewrites:
            if not used:
                resp.warnings.append("no target word used")
            if unexpected:
                resp.warnings.append(f"out-of-vocab content words: {unexpected}")
            return resp
        problems = []
        if not used:
            problems.append("you must use at least one TARGET item")
        if unexpected:
            problems.append("avoid these out-of-vocab words: " + ", ".join(unexpected))
        extra = "Your previous reply broke the rules (" + "; ".join(problems) + "). Rewrite it."
    assert resp is not None
    return resp


HARVEST_SYSTEM = (
    "From the Korean conversation below, propose 3 to 5 NEW useful Korean lexical "
    "chunks the learner reached for or would benefit from next. Return ONLY a JSON "
    'object {"chunks":[{"type","ko","en","gloss"}]} with type in '
    "sentence|noun_verb|grammar. Avoid items in the KNOWN list. No markdown fences."
)


def harvest_chunks(
    *, history: list[dict[str, str]], provider: LLMProvider,
    known: Optional[set[str]] = None, model: Optional[str] = None,
    max_n: int = 5,
) -> list[Chunk]:
    known = known or set()
    model = model or config.SPARRING_MODEL
    convo = "\n".join(f"{t.get('role', '?')}: {t.get('text', '')}" for t in history)
    user = f"KNOWN (avoid): {{{', '.join(sorted(known)[:400])}}}\n\nConversation:\n{convo}"
    data = provider.complete_json(system=HARVEST_SYSTEM, user=user, model=model, max_tokens=800)
    raw = data.get("chunks") if isinstance(data, dict) else None
    out: list[Chunk] = []
    if isinstance(raw, list):
        for rc in raw:
            if not isinstance(rc, dict):
                continue
            t, ko = rc.get("type"), rc.get("ko")
            if t in config.CHUNK_TYPES and isinstance(ko, str) and ko.strip():
                out.append(Chunk(type=t, ko=ko.strip(),
                                 en=str(rc.get("en", "")).strip(),
                                 gloss=str(rc.get("gloss", "")).strip()))
    return dedupe_chunks(out, known)[:max_n]


# --- service layer (persistence) ----------------------------------------

def start_session(*, segment_id: Optional[int] = None) -> SparSession:
    with db.session_scope() as s:
        sess = SparSession(segment_id=segment_id)
        s.add(sess)
        s.flush()
        return sess


def record_turn(
    session_id: int, role: str, *, text_ko: str = "",
    glosses: Optional[dict] = None, feedback: Optional[dict] = None,
    targets_used: Optional[list] = None,
) -> SparTurn:
    with db.session_scope() as s:
        turn = SparTurn(
            session_id=session_id, role=role, text_ko=text_ko,
            gloss_json=json.dumps(glosses or {}, ensure_ascii=False),
            feedback_json=json.dumps(feedback or {}, ensure_ascii=False),
            targets_used_json=json.dumps(targets_used or [], ensure_ascii=False),
        )
        s.add(turn)
        s.flush()
        return turn


def session_history(session_id: int) -> list[dict[str, str]]:
    with db.session_scope() as s:
        turns = s.exec(
            select(SparTurn).where(SparTurn.session_id == session_id).order_by(SparTurn.id)
        ).all()
    return [{"role": t.role, "text": t.text_ko} for t in turns]
