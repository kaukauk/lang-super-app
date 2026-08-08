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
    # Only the sparring harvest fills these, for the Korean-only chunk cards: the
    # ending's citation notation as a LABEL, and the plain form it attached to, so
    # the card can show an anchor -> target derivation the way the KBA decks do.
    ending: str = ""
    anchor: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "ko": self.ko, "en": self.en, "gloss": self.gloss,
                "ending": self.ending, "anchor": self.anchor}


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
    '- "primer_md": a detailed scene-by-scene English walkthrough (markdown). Give '
    "each scene a short `### heading`, then 3-6 sentences covering: who is present "
    "and how they relate, where they are, what each one WANTS, the specific beats in "
    "order (not a vague gist), and how the scene resolves or hands off to the next. "
    "Name characters consistently so they can be followed across scenes. ENGLISH "
    "ONLY — never include Korean text or transliteration. Explicitly call out "
    "non-literal idioms, abstract bets/arguments, jokes that depend on wordplay, and "
    "shifts in tone or formality, since those are what the learner will otherwise "
    "mishear. The learner reads this BEFORE watching with no subtitles, so it must "
    "carry enough detail to follow the plot from sound alone.\n"
    '- "chunks": an array of EXACTLY 15 lexical chunks extracted AS THEY APPEAR '
    "in the dialogue (NOT dictionary form), split as:\n"
    f"    * {PER_TYPE} with type \"sentence\": SHORT, simple, high-frequency sentences "
    "(aim for ~3-6 words; avoid long or complex sentences) useful in daily conversation.\n"
    f"    * {PER_TYPE} with type \"noun_verb\": a common noun attached to its natural verb (a collocation).\n"
    f"    * {PER_TYPE} with type \"grammar\": a verb phrase highlighting ONE specific grammatical ending or particle used in the segment. "
    "The ending must appear INSIDE a real phrase from the dialogue with a real verb "
    "attached (e.g. \"가려면\", NOT \"-려면\"). NEVER return a bare ending or a "
    "citation form — no leading dash or tilde, no slashes, no parentheses, no "
    "standalone jamo. Name the ending in the gloss instead.\n"
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


# Dictionary-notation marks that never occur in speech: a leading dash/tilde, the
# slash of "-아/어서", the parens of "-(으)ㄴ", and bare jamo (ㄴ, ㄹ, ㅁ).
_CITATION_MARKS = re.compile(r"^\s*[-~–—]|[/()（）]")
_JAMO = re.compile(r"[ㄱ-ㆎ]")


def is_usable_grammar(ko: str) -> bool:
    """True if a grammar chunk is a real phrase rather than a bare ending.

    A card reading "-려면 / if (you) want to" teaches a slot with nothing in it —
    unreviewable and unpronounceable. A usable one carries the ending on an actual
    verb ("가려면"), which is what having a content lemma tests for."""
    if _CITATION_MARKS.search(ko) or _JAMO.search(ko):
        return False
    return bool(analyzer.content_lemmas(ko))


def drop_citation_grammar(
    chunks: list[Chunk], warnings: Optional[list[str]] = None,
) -> list[Chunk]:
    """Filter grammar chunks down to usable ones. The caller's top-up pass refills
    the gap, so a rejection costs a slot rather than the card."""
    kept, dropped = [], []
    for c in chunks:
        (kept if (c.type != "grammar" or is_usable_grammar(c.ko)) else dropped).append(c)
    if dropped and warnings is not None:
        warnings.append("dropped bare-ending grammar chunks: "
                        + ", ".join(c.ko for c in dropped))
    return kept


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
            # the primer and all 15 chunks share this budget; a detailed primer needs
            # the headroom or the chunks get truncated into invalid JSON
            model=model, max_tokens=4096,
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
    # drop bare endings, dedupe against known deck, then top up any thinned categories
    result.chunks = dedupe_chunks(
        drop_citation_grammar(result.chunks, result.warnings), known)
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
        if "grammar" in short:
            topup_extra += (" For grammar, the ending must sit on a real verb from the "
                            "dialogue — a bare ending like \"-려면\" will be rejected.")
        data = provider.complete_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(transcript, list(known | selected), extra=topup_extra),
            model=model, max_tokens=1024,
        )
        try:
            more = parse_result(data).chunks
        except LLMError:
            break
        more = dedupe_chunks(drop_citation_grammar(more), known | selected)
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


# --- AV target selection (over the Whisper sentence list) ----------------

# Below this a "clause" is a fragment, not something worth drilling on its own.
_MIN_CLAUSE_CHARS = 4
# A production target shorter than this is not really a test: "왔어요" with every
# content word already supplied by the context line asks you to conjugate, not to
# say anything.
_MIN_TARGET_CHARS = 8


def split_clauses(text: str) -> list[str]:
    """Break a sentence at its clause joints, using morphology rather than spelling.

    Two clauses welded with -고 or -지만 make one card that tests two grammar points
    and several words at once, with a single pass/fail: miss any part and you cannot
    tell which part you missed. Splitting lets one clause be the thing to produce
    while the rest stays visible as context.

    The joint has to be identified by POS tag, not by how the word ends. Kiwi tags
    the connective in 먹고 as EC but the particle in 친구하고 ("with a friend") as
    JKB — identical spelling, opposite meaning. A spelling rule gets both wrong: it
    splits 친구하고 갔어요 in the middle of a phrase, and misses 먹고 because the
    token is short. Guarding by token length trades one error for the other.

    Returns [text] unchanged when there is no clean joint."""
    if not text or not text.strip():
        return []
    morphs = [m for m in analyzer.analyze(text) if m.start >= 0]
    cuts: list[int] = []
    for m in morphs:
        if not m.tag.startswith("EC"):
            continue
        head, tail = text[:m.end].strip(), text[m.end:].strip()
        # Don't strand a fragment on either side of the split.
        if len(head) >= _MIN_CLAUSE_CHARS and len(tail) >= _MIN_CLAUSE_CHARS:
            cuts.append(m.end)
    if not cuts:
        return [text]
    clauses, prev = [], 0
    for c in cuts:
        clauses.append(text[prev:c].strip())
        prev = c
    clauses.append(text[prev:].strip())
    return [c for c in clauses if c] or [text]


def drillable_part(text: str) -> tuple[str, str]:
    """Split a sentence into (lead-in, the clause worth producing).

    The final clause carries the sentence's main predicate, so it is the piece that
    makes a coherent production target; everything before it becomes context the
    card can show without asking you to recall it.

    A target needs a floor as well as a ceiling. Chains of three or four clauses
    end on something like "왔어요" — grammatically a clause, but as a card it asks
    you to produce a bare verb with every content word already given. Clauses are
    merged back in until the target is a real utterance."""
    clauses = split_clauses(text)
    if len(clauses) < 2:
        return "", text
    take = 1
    while take < len(clauses) and len(" ".join(clauses[-take:])) < _MIN_TARGET_CHARS:
        take += 1
    return " ".join(clauses[:-take]), " ".join(clauses[-take:])


@dataclass
class TargetPick:
    index: int           # index into the Whisper sentence list
    ko: str              # the target sentence (as the LLM echoed it)
    explanation_en: str  # short, context-oriented English (back-of-card + primer)


TARGET_SYSTEM = (
    "You are a Korean tutor choosing the most useful sentences for a learner to "
    "drill from a cartoon scene they just watched. You receive the scene's "
    "sentences, numbered, transcribed from the audio.\n"
    "Pick {n} TARGET sentences that are COMPLETE, natural sentences useful in daily "
    "conversation — each must stand on its own (a clear subject/topic AND a verb or "
    "predicate). Prefer ~4-10 words. STRICTLY AVOID interjections, names, single "
    "words, or fragments (e.g. reject things like 'hey', 'stop', 'weightlifting', "
    "'Player Kim'). Avoid sentences whose vocabulary the learner already knows.\n"
    "Some entries carry a bracketed lead-in. That is CONTEXT ONLY: use it to judge "
    "what the line means, but your explanation must describe ONLY the numbered "
    "line. Do not translate or summarise the lead-in — the learner is given it "
    "already and is not being asked to produce it.\n"
    'Return ONLY JSON: {"targets":[{"index":<int from the list>,"ko":"<the sentence '
    'exactly as numbered>","explanation_en":"<one short sentence: what is happening '
    "/ what the NUMBERED LINE means IN CONTEXT — NOT a stiff word-for-word "
    "translation, and NOT a translation of the lead-in>\"}]}. "
    "Order them best-first. No markdown fences."
)


def build_targets_prompt(sentences: list[str], avoid_forms: list[str], n: int,
                         leads: Optional[list[str]] = None) -> str:
    """`sentences` are the lines that will BECOME cards; `leads` are their lead-ins.

    The two are listed separately so the model can judge a line in context while
    explaining only the line itself. Merging them is what produced explanations
    covering material the card never asks for."""
    avoid = ", ".join(sorted(set(avoid_forms))[:400]) or "(none yet)"
    rows = []
    for i, s in enumerate(sentences):
        lead = (leads[i] if leads and i < len(leads) else "").strip()
        rows.append(f"{i}: {s}" + (f"\n   (lead-in, context only: {lead})" if lead else ""))
    return "\n".join([
        f"Already-known items to AVOID picking: {{{avoid}}}",
        f"Return your {n} best COMPLETE-sentence targets, best first.",
        "",
        "Scene sentences (index: text):",
        "\n".join(rows),
    ])


def is_full_sentence(text: str) -> bool:
    """Reject fragments/interjections (e.g. '야 역도.', '스톱.'): a real sentence has a
    predicate (verb/adjective/copula or a sentence-final ending), at least two words,
    and a few syllables."""
    t = text.strip().rstrip(".?!… ")
    morphs = analyzer.analyze(text)
    has_predicate = any(m.tag.split("-", 1)[0] in ("VV", "VA", "VCP", "VCN") for m in morphs) \
        or bool(re.search(r"(다|요|까|죠|네|군|구나|세요|거든|잖아|는데|니|어|아)$", t))
    return has_predicate and len(_HANGUL.findall(text)) >= 5 and len(text.split()) >= 2


def _align_index(pick_ko: str, pick_index: int, sentences: list[str]) -> int:
    """Map an LLM-returned target back to a real sentence index. Trust the given
    index if it matches; else exact normalized match; else best lemma overlap."""
    n = len(sentences)
    pk = _norm(pick_ko)
    if 0 <= pick_index < n and _norm(sentences[pick_index]) == pk:
        return pick_index
    for i, s in enumerate(sentences):
        if _norm(s) == pk:
            return i
    plem = analyzer.lemma_set(pick_ko)
    if plem:
        best, best_i = 0.0, -1
        for i, s in enumerate(sentences):
            slem = analyzer.lemma_set(s)
            if not slem:
                continue
            score = len(plem & slem) / len(plem | slem)
            if score > best:
                best, best_i = score, i
        if best_i >= 0 and best >= 0.34:
            return best_i
    return pick_index if 0 <= pick_index < n else -1


def pick_targets(
    sentences: list[str], provider: LLMProvider, *, known: Optional[set[str]] = None,
    n: int = config.AV_TARGETS_PER_SEGMENT, model: Optional[str] = None,
) -> list[TargetPick]:
    """Choose `n` target sentences from the Whisper sentence list, each aligned back
    to its index so timing + context come straight from Whisper."""
    if not sentences:
        return []
    known = known or set()
    model = model or config.EXTRACTION_MODEL

    # Only offer sentences short enough to be worth drilling. The sentence-grouping
    # caps are on DURATION, which is the wrong measure here: a fast speaker fits 62
    # characters into 6.6s, so it clears every timing check and is still a mouthful
    # to produce from memory. Filtering the candidate list rather than rejecting
    # afterwards means the LLM never has to be told twice.
    #
    # Indices returned to the caller stay indices into the FULL list, because
    # lead-up context is resolved from neighbours — a target may be long-winded
    # while the line before it is exactly the context needed.
    cap = config.AV_TARGET_MAX_CHARS
    # Measure the clause that would actually be drilled, not the whole sentence.
    # A two-clause line is long on the page but its target is one clause, so
    # judging the sentence would reject exactly the connective grammar (-고, -지만,
    # -는데) that is worth learning.
    eligible = [i for i, s in enumerate(sentences)
                if len(drillable_part(s)[1].strip()) <= cap]
    if len(eligible) < n:
        # A scene of long sentences would otherwise yield almost no targets. Take
        # the shortest available instead, so a hard day still produces a full set.
        eligible = sorted(range(len(sentences)), key=lambda i: len(sentences[i]))[:n]
        eligible.sort()
    # Offer the CLAUSE that will become the card, with its lead-in alongside as
    # context. Judging and explaining the whole sentence produced hints that
    # translated material the card never asks for — the learner reads the lead-in,
    # so an explanation covering it gives away the answer to a different question.
    parts = [drillable_part(sentences[i]) for i in eligible]
    leads = [lead for lead, _ in parts]
    subset = [clause for _, clause in parts]

    n = min(n, len(subset))
    # over-request so we can drop fragments and still reach n good full sentences
    n_request = min(len(subset), n + 5)
    data = provider.complete_json(
        system=TARGET_SYSTEM.replace("{n}", str(n_request)),
        user=build_targets_prompt(subset, list(known), n_request, leads),
        model=model, max_tokens=1600,
    )
    raw = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise LLMError("target selection: missing 'targets' array")
    picks: list[TargetPick] = []
    seen: set[int] = set()
    for rt in raw:
        if not isinstance(rt, dict):
            continue
        ko = str(rt.get("ko", "")).strip()
        if not ko:
            continue
        sub_idx = _align_index(ko, int(rt.get("index", -1) or -1), subset)
        if sub_idx < 0:
            continue
        idx = eligible[sub_idx]           # back to an index into the full list
        if idx in seen:
            continue
        # Judge the CLAUSE, since that is what becomes the card. A compound
        # sentence can read as a fine sentence while its tail clause is a fragment.
        if not is_full_sentence(subset[sub_idx]):      # drop interjections/fragments
            continue
        seen.add(idx)
        picks.append(TargetPick(index=idx, ko=sentences[idx],
                                explanation_en=str(rt.get("explanation_en", "")).strip()))
        if len(picks) >= n:
            break
    return picks


def accept_chunks(
    chunks: list[Chunk], *, segment_id: Optional[int] = None,
    deck_name: str = "Korean", today_ord: Optional[int] = None,
) -> list[Chunk]:
    """Accepted chunks go to Anki and nowhere else.

    This used to also write rows into the app's own Card table, so the same chunk
    lived in two stores with nothing keeping them in step — exactly the mismatch
    between the site and Anki being removed here. Known-vocabulary lookups now read
    Anki directly (services/known.py), so that table has no reader left.

    Returns the chunks unchanged. `segment_id` and `today_ord` are accepted and
    ignored so existing callers and the CLI keep working; there is no in-app store
    to attribute them to and no schedule left to move."""
    return list(chunks)
