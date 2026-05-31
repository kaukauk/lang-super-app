"""Korean morphological analysis, behind a tiny swappable interface.

Backed by Kiwi (kiwipiepy): real tokenization + POS tagging + lemmatization, so
we never hand-maintain particle/ending lists. The rest of the codebase only
touches `analyze`, `lemmas`, `content_lemmas`, and `lemma_set` — swap the backend
here and nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass

# Parts of speech we treat as meaningful "content" vocabulary.
#   NNG/NNP general & proper nouns, VV verb, VA adjective, MAG adverb,
#   XR root, SL foreign word, SH hanja, SN number.
CONTENT_TAGS = frozenset({"NNG", "NNP", "VV", "VA", "MAG", "XR", "SL", "SH", "SN"})
# Verbal POS whose dictionary form is the stem + "다".
_VERBAL = frozenset({"VV", "VA"})

_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi  # lazy: model load ~0.3s, only when first needed
        _kiwi = Kiwi()
    return _kiwi


def _base_tag(tag: str) -> str:
    """Kiwi marks irregulars as VV-I / VA-I / VV-R etc. Collapse to the base tag."""
    return tag.split("-", 1)[0]


@dataclass(frozen=True)
class Morph:
    surface: str    # the morpheme as it appeared
    tag: str        # Kiwi POS tag (may carry a -I/-R irregularity suffix)
    lemma: str      # dictionary form (verbs/adjectives end in 다)

    @property
    def is_content(self) -> bool:
        return _base_tag(self.tag) in CONTENT_TAGS


def _lemma_of(token) -> str:
    base = getattr(token, "lemma", None) or token.form
    if _base_tag(token.tag) in _VERBAL:
        return base if base.endswith("다") else base + "다"
    return base


def analyze(text: str) -> list[Morph]:
    if not text or not text.strip():
        return []
    return [
        Morph(surface=t.form, tag=t.tag, lemma=_lemma_of(t))
        for t in _get_kiwi().tokenize(text)
    ]


def lemmas(text: str) -> list[str]:
    return [m.lemma for m in analyze(text)]


def content_lemmas(text: str) -> list[str]:
    """Lemmas of content morphemes only (nouns/verbs/adjectives/adverbs/...)."""
    return [m.lemma for m in analyze(text) if m.is_content]


def lemma_set(text: str) -> set[str]:
    return set(content_lemmas(text))


def content_lemmas_of_many(forms) -> set[str]:
    """Union of content lemmas over many surface forms (deck vocab -> known set)."""
    out: set[str] = set()
    for f in forms:
        out.update(content_lemmas(f))
    return out
