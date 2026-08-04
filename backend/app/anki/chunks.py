"""The harvested-chunk note type: a Korean-only production card.

Sits alongside the collection's existing KBA decks rather than duplicating them.
Those already cover the atomised layers exhaustively — isolated vocab, and
conjugation FORM drilled as anchor -> target. What they don't cover is assembly
under production pressure, which is the only thing a harvested chunk is evidence
of: it comes from a moment in sparring where the learner reached for something
and couldn't get it out.

So one card per chunk, not a vocab card plus a grammar card. Splitting a chunk
hands back three fragments the collection already owns (the noun, the verb, the
ending) and throws away the thing that was actually missing — which particle,
which ending, in which collocation.

The front is the chunk with one span blanked, which is what makes a single card
type sufficient: the blank position chooses what gets drilled. Blank the ending
and it is a grammar card; blank the content word and it is a vocab card. When the
chunk came from a correction we know exactly which span to blank, because we know
exactly what the learner got wrong.

No English anywhere, matching the KBA decks. The conjugation is explained the way
they explain it: an ending chip plus an anchor -> target derivation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

from .. import config

# Anchor and Target are separate fields with the arrow living in the TEMPLATE, the
# way the KBA Conjugation note type does it. An arrow baked into the data would
# freeze a formatting choice into content and, more practically, would stop a TTS
# add-on from reading either side on its own. Every field here is either clean
# Korean or pure notation, so audio can be generated from Chunk (or Anchor, or
# Target) without a filter step.
FIELDS = ["Prompt", "Chunk", "Ending", "Anchor", "Target", "Audio"]
TEMPLATE_NAME = "Chunk Production"

BLANK = "______"
SUB_BLANK = "__"     # inside a word, for a particle/ending a correction flagged


# --- blank selection ------------------------------------------------------

def _eojeols(text: str) -> list[str]:
    """Whitespace-separated words, which in Korean are the natural blank unit."""
    return [w for w in text.strip().split() if w]


def changed_span(original: str, corrected: str) -> str:
    """The part of `corrected` that differs from what the learner wrote.

    This is the whole reason the corrections work matters here: a chunk harvested
    after a correction knows precisely which element failed, so the blank can land
    on it rather than on a guess."""
    if not original or not corrected:
        return ""
    matcher = SequenceMatcher(None, original, corrected)
    parts = [corrected[j1:j2] for tag, _i1, _i2, j1, j2 in matcher.get_opcodes()
             if tag in ("replace", "insert")]
    return max(parts, key=len).strip() if parts else ""


def _hangul(s: str) -> str:
    return re.sub(r"[^가-힣]", "", s)


def anchor_word_index(chunk_ko: str, anchor: str) -> int:
    """Index of the word in `chunk_ko` that continues the anchor's stem, or -1.

    Matched on a common prefix rather than containment because the anchor is a 해요
    form and the chunk is conjugated differently: 생각해요 vs 생각할 수 있어요 share
    only 생각. Two syllables is the floor — one would match on coincidence."""
    stem = _hangul(anchor).rstrip("요")
    if len(stem) < 2:
        return -1
    best_len, best_i = 0, -1
    for i, word in enumerate(_eojeols(chunk_ko)):
        wh = _hangul(word)
        n = 0
        while n < min(len(stem), len(wh)) and stem[n] == wh[n]:
            n += 1
        if n >= 2 and n > best_len:
            best_len, best_i = n, i
    return best_i


def predicate_span(chunk_ko: str, anchor: str) -> str:
    """The conjugated predicate the anchor became — from the anchor's word to the end
    of the chunk. "들어가요" in "위험한 곳에 들어가면 안 돼요" gives "들어가면 안 돼요",
    which is the transformation worth showing, not the whole sentence."""
    i = anchor_word_index(chunk_ko, anchor)
    return " ".join(_eojeols(chunk_ko)[i:]) if i >= 0 else ""


def choose_blank(chunk_ko: str, *, corrected_from: str = "", anchor: str = "") -> str:
    """Pick the span to hide on the front, best evidence first:

    1. what a correction changed — the demonstrated gap
    2. the predicate the anchor became, when we are explaining a conjugation
    3. the final word, which in a Korean chunk is the predicate

    Never returns the whole chunk: a card with nothing showing is not a prompt."""
    words = _eojeols(chunk_ko)
    if not words:
        return ""
    if corrected_from:
        span = changed_span(corrected_from, chunk_ko)
        if span and span != chunk_ko.strip():
            return span
    if anchor:
        span = predicate_span(chunk_ko, anchor)
        # a predicate span covering everything would blank the entire card
        if span and span != chunk_ko.strip():
            return span
    return words[-1] if len(words) > 1 else ""


def build_prompt(chunk_ko: str, blank: str) -> str:
    """The chunk with `blank` replaced by underscores.

    A blank covering whole words gets a full-width run; a blank inside a word — a
    particle, from a correction — gets a short one so it reads as a missing ending
    rather than a missing word. "밥을 먹었어요" blanked on 을 must still show 밥, or
    the card stops being about that noun and any object would answer it."""
    if not blank or blank not in chunk_ko:
        return chunk_ko
    marker = BLANK if blank in _eojeols(chunk_ko) or " " in blank else SUB_BLANK
    return chunk_ko.replace(blank, marker, 1)


# --- note -----------------------------------------------------------------

@dataclass
class ChunkNote:
    chunk_ko: str
    ending: str = ""            # citation notation, e.g. -(으)면 안 돼요 — a LABEL
    anchor: str = ""            # plain form the ending was attached to, e.g. 들어가요
    corrected_from: str = ""    # the learner's original wording, when there was one
    audio_file: str = ""        # TTS of the full chunk, in collection.media
    tags: list[str] = field(default_factory=list)

    def blank(self) -> str:
        return choose_blank(self.chunk_ko, corrected_from=self.corrected_from,
                            anchor=self.anchor)

    def target(self) -> str:
        """The conjugated predicate the anchor became — the right-hand side of the
        derivation. Scoped to the predicate rather than the whole chunk, which is
        what makes it a derivation and not a restatement.

        Empty unless we know the anchor: a fabricated one would teach a form the
        learner never heard, and the template hides the whole row without it."""
        if not self.anchor:
            return ""
        return predicate_span(self.chunk_ko, self.anchor) or self.chunk_ko

    def fields(self) -> dict[str, str]:
        return {
            "Prompt": build_prompt(self.chunk_ko, self.blank()),
            "Chunk": self.chunk_ko,
            "Ending": self.ending,
            "Anchor": self.anchor,
            "Target": self.target(),
            "Audio": f"[sound:{self.audio_file}]" if self.audio_file else "",
        }


_CSS = """.card {
  font-family: "Noto Sans KR", system-ui, sans-serif;
  font-size: 24px;
  text-align: center;
  color: #1a1a1a;
  background: #fbfbfb;
}
.chip {
  display: inline-block; font-size: 15px; color: #4a5568;
  background: #edf2f7; border-radius: 999px; padding: 3px 12px; margin-bottom: 14px;
}
.chunk { font-size: 30px; font-weight: 600; line-height: 1.6; }
.prompt { font-size: 30px; font-weight: 600; line-height: 1.6; color: #2d3748; }
.derivation { font-size: 18px; color: #718096; margin-top: 14px; }
.arrow { margin: 0 8px; color: #a0aec0; }
hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
"""

# No English on either side, matching the KBA decks. The front is a production
# prompt: the chip names the pattern, the blank is what you have to supply.
_FRONT = """{{#Ending}}<div class="chip">{{Ending}}</div>{{/Ending}}
<div class="prompt">{{Prompt}}</div>
"""

# No {{Ending}} chip here, unlike the KBA Conjugation back this is modelled on.
# That deck's back is "the question with the answer filled in" (chip + anchor ->
# target), so repeating the chip completes the prompt. This back resolves the
# prompt a different way — the full chunk IS the blank filled in — which leaves the
# chip restating rather than resolving. It stays on the front, where it does real
# work: it is what narrows "위험한 곳에 ______" to a single correct answer, and it
# carries the (으) alternation that a single derived form never shows.
#
# Audio only on the back — on the front it would read the answer aloud.
_BACK = """<div class="chunk">{{Chunk}}</div>
{{#Anchor}}<div class="derivation"><span class="anchor">{{Anchor}}</span><span class="arrow">&rarr;</span><span class="target">{{Target}}</span></div>{{/Anchor}}
{{Audio}}
"""


def build_model_payload(model: Optional[str] = None) -> dict[str, Any]:
    return {
        "modelName": model or config.ANKI_CHUNK_MODEL,
        "inOrderFields": list(FIELDS),
        "css": _CSS,
        "isCloze": False,
        "cardTemplates": [{"Name": TEMPLATE_NAME, "Front": _FRONT, "Back": _BACK}],
    }


def build_note_payload(note: ChunkNote, deck: Optional[str] = None,
                       model: Optional[str] = None) -> dict[str, Any]:
    return {
        "deckName": deck or config.ANKI_CHUNK_DECK,
        "modelName": model or config.ANKI_CHUNK_MODEL,
        "fields": note.fields(),
        "tags": list(note.tags),
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
    }
