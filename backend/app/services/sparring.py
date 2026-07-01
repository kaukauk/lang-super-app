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
from . import analyzer, ingest, transcribe
from .extraction import Chunk, dedupe_chunks

# n+1: a reply may introduce AT MOST this many unknown content words (the learning
# stretch — ideally the day's target). More than this triggers a rewrite.
MAX_NEW_WORDS = 1


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
    "You are a friendly Korean conversation partner role-playing with the learner, "
    "continuing the scene of a cartoon they just watched. STAY IN CHARACTER as your "
    "assigned role; never break role. Speak ONLY in polite 해요체.\n"
    "Rules:\n"
    "- reply_ko: 1-2 short sentences ending with exactly ONE question.\n"
    "- Keep replies at an n+1 level: build them from the ALLOWED content words the "
    "learner already knows, plus AT MOST ONE new content word/verb (ideally one of "
    "today's TARGET items) as the learning stretch. Never use two or more new "
    "content words. Particles/function words are always fine.\n"
    "- Prefer to include one of today's TARGET items. If the flow conflicts with "
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
    role: str = "", wind_down: bool = False,
) -> str:
    hist = history or []
    lines = [
        f"Your role (stay in character): {role or '(a friendly character from the scene)'}",
        f"Scene so far (English): {scene or '(start of conversation)'}",
        f"Today's TARGET items (use at least one): {{{', '.join(targets) or '(none)'}}}",
        f"ALLOWED content words: {{{', '.join(sorted(set(allowed))[:400]) or '(none)'}}}",
        "",
        "Recent turns:",
    ]
    for turn in hist[-6:]:
        lines.append(f"  {turn.get('role', '?')}: {turn.get('text', '')}")
    if wind_down:
        lines += ["", "The ~15-minute session is nearly up — steer toward a natural "
                  "close (wrap the scene, say a friendly goodbye) in this reply."]
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
    model: Optional[str] = None, max_rewrites: int = 2, role: str = "",
    wind_down: bool = False,
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
                                   history=history, user_text=user_text, extra=extra,
                                   role=role, wind_down=wind_down),
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
        # n+1: tolerate up to MAX_NEW_WORDS unknown content words (the stretch).
        ok = bool(used) and len(unexpected) <= MAX_NEW_WORDS
        if ok or attempt >= max_rewrites:
            if not used:
                resp.warnings.append("no target word used")
            if len(unexpected) > MAX_NEW_WORDS:
                resp.warnings.append(f"too many new content words ({len(unexpected)}): {unexpected}")
            elif unexpected:
                resp.warnings.append(f"new word (n+1): {unexpected}")
            return resp
        problems = []
        if not used:
            problems.append("you must use at least one TARGET item")
        if len(unexpected) > MAX_NEW_WORDS:
            problems.append(
                f"introduce AT MOST {MAX_NEW_WORDS} new content word; these are too "
                f"many: {', '.join(unexpected)} — keep one (a target) and replace the rest "
                "with ALLOWED words")
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


# --- opener: the Anchor + Prompt states ---------------------------------

@dataclass
class SparOpening:
    anchor_ko: str = ""              # State 1: the scene's last lines, re-established
    role_en: str = ""               # State 2: the character assigned to the learner
    prompt_en: str = ""             # State 2: hands the learner the floor
    scene_en: str = ""              # short English scene description (continuity)
    glosses: dict[str, str] = field(default_factory=dict)


OPENER_SYSTEM = (
    "You set up a Korean role-play for a learner who just watched a cartoon scene. "
    "You are given the scene's final Korean lines.\n"
    "Return ONLY a JSON object with keys:\n"
    '- "anchor_ko": the final 2-3 Korean lines, lightly cleaned, to re-establish the '
    "scene (Korean only).\n"
    '- "role_en": the character you assign the LEARNER to play (short, e.g. "the '
    'customer at the cafe").\n'
    '- "prompt_en": one or two sentences in English assigning that role and handing '
    'them the floor (e.g. "You are the customer. The barista is waiting. What do you '
    'say?").\n'
    '- "scene_en": a one-line English description of the situation for continuity.\n'
    '- "glosses": object mapping each content word in anchor_ko to a short English '
    "gloss.\n"
    "No markdown fences."
)


def parse_opening(data: dict[str, Any], *, fallback_anchor: str = "") -> SparOpening:
    if not isinstance(data, dict):
        raise LLMError("opening response is not an object")
    glosses = data.get("glosses") if isinstance(data.get("glosses"), dict) else {}
    return SparOpening(
        anchor_ko=str(data.get("anchor_ko", "")).strip() or fallback_anchor,
        role_en=str(data.get("role_en", "")).strip(),
        prompt_en=str(data.get("prompt_en", "")).strip(),
        scene_en=str(data.get("scene_en", "")).strip(),
        glosses={str(k): str(v) for k, v in glosses.items()},
    )


def segment_closing_lines(segment_id: int, *, n: int = 3) -> list[str]:
    """The last n Korean lines of a segment — accurate Whisper sentences if we've
    transcribed it, else the last YouTube caption cues."""
    seg = ingest.get_segment(segment_id)
    if seg is None:
        return []
    video = ingest.get_video(seg.video_id)
    if video is not None:
        cached = transcribe.load_cached(video.youtube_id, segment_id)
        if cached:
            return [s.text.strip() for s in cached[-n:] if s.text.strip()]
    cues = ingest.get_cues_in_range(seg.video_id, seg.start_s, seg.end_s)
    return [c.text.replace("\n", " ").strip() for c in cues[-n:] if c.text.strip()]


def spar_open(
    session_id: int, *, provider: LLMProvider, segment_id: Optional[int] = None,
    model: Optional[str] = None,
) -> SparOpening:
    """States 1+2: replay the scene's last lines, assign the learner a character,
    and hand them the floor. Persists the role on the session and records the
    opening as the session's first assistant turn."""
    model = model or config.SPARRING_MODEL
    lines = segment_closing_lines(segment_id, n=3) if segment_id is not None else []
    fallback = " ".join(lines)
    user = (
        "Scene's final Korean lines:\n"
        + ("\n".join(f"- {ln}" for ln in lines) if lines else "(none available)")
    )
    data = provider.complete_json(system=OPENER_SYSTEM, user=user, model=model, max_tokens=500)
    opening = parse_opening(data, fallback_anchor=fallback)
    set_session_role(session_id, role_en=opening.role_en, scene_en=opening.scene_en)
    # the opening becomes the first assistant turn so history carries it forward
    record_turn(session_id, "assistant",
                text_ko=opening.anchor_ko, glosses=opening.glosses,
                feedback={"role_en": opening.role_en, "prompt_en": opening.prompt_en,
                          "scene_en": opening.scene_en, "kind": "opening"})
    return opening


# --- service layer (persistence) ----------------------------------------

def start_session(*, segment_id: Optional[int] = None) -> SparSession:
    with db.session_scope() as s:
        sess = SparSession(segment_id=segment_id)
        s.add(sess)
        s.flush()
        return sess


def set_session_role(session_id: int, *, role_en: str = "", scene_en: str = "") -> None:
    with db.session_scope() as s:
        sess = s.get(SparSession, session_id)
        if sess is None:
            raise ValueError(f"no spar session {session_id}")
        sess.role_en = role_en
        sess.scene_en = scene_en
        s.add(sess)


def get_session(session_id: int) -> Optional[SparSession]:
    with db.session_scope() as s:
        return s.get(SparSession, session_id)


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
