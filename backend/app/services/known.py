"""What the learner already knows — read from Anki, which is the master database.

Previously this came from the app's own Card table, which meant two stores of the
same thing that drifted the moment either side was edited. Anki is where the cards
actually live and get reviewed, so it is the only place asked.

It is also far more accurate: the app's table held a handful of rows while the
collection holds thousands, so "avoid re-teaching what they know" was working off
a tiny sample and re-teaching constantly.

The on-disk file here is a CACHE, not a second source of truth: it is only read
when Anki cannot be reached, and it exists so closing Anki degrades extraction
rather than breaking it. It is never written to by anything but a successful read
from Anki.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..anki.connect import AnkiConnectClient

_HANGUL = re.compile(r"[가-힣]")
_BOLD = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)
_LATIN = re.compile(r"[A-Za-z]")
# notesInfo on a few thousand notes is a large payload; ask in slices.
_BATCH = 500


def cache_path(seen_only: bool = False) -> Path:
    # Separate files: the two readings of "known" differ by thousands of forms here,
    # and one overwriting the other's cache would hand a caller the wrong answer the
    # next time Anki happened to be closed.
    name = "known_forms_seen.json" if seen_only else "known_forms.json"
    return Path(config.MEDIA_CACHE_DIR) / name


def strip_field(text: str) -> str:
    """A field value as plain text: no media tags, no HTML, no entities."""
    text = re.sub(r"\[sound:[^\]]*\]", " ", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _forms_from_note(info: dict[str, Any]) -> set[str]:
    """Every Korean-bearing field value on a note, plus any bolded span inside one.

    Field names differ per note type — Basic uses Front, KBA Conjugation uses
    Anchor/Target, ours uses Chunk — so select on content rather than maintaining a
    per-model map that silently misses any note type added later.

    The bolded spans matter because a card whose Korean side is a whole sentence
    otherwise contributes nothing: a sentence never equals a single word, so nothing
    in it is ever recognised. Bold is the note pointing at the one word it is
    actually about — on a mined cloze it is exactly the word that was clicked — and
    without reading it, mining 제국 as a cloze would leave 제국 reading as new
    forever, and blocking its neighbours' clozes forever with it."""
    out: set[str] = set()
    for value in (info.get("fields") or {}).values():
        raw = (value or {}).get("value", "")
        for span in _BOLD.findall(raw):
            text = strip_field(span)
            if _is_korean_form(text):
                out.add(text)
        text = strip_field(raw)
        if _is_korean_form(text):
            out.add(text)
    return out


def _is_korean_form(text: str) -> bool:
    """Reject fields that merely CONTAIN Korean.

    Definition fields routinely read "(1) it is possible that 아마도 2) how on earth"
    — mostly English with a Korean word embedded. Taking those whole would feed the
    LLM's avoid-list and the morphological analyzer sentences of English, so require
    the value to be predominantly Hangul and short enough to be a real surface form
    rather than a gloss."""
    text = text.strip()
    if not text or len(text) > 80:
        return False
    dense = re.sub(r"\s", "", text)
    if not dense:
        return False
    return len(_HANGUL.findall(dense)) / len(dense) >= 0.5


def deck_query(deck: str, seen_only: bool = False) -> str:
    """The Anki search for a deck, optionally narrowed to cards already studied."""
    query = f'deck:"{deck}"'
    if seen_only and config.ANKI_SEEN_FILTER:
        return f"{query} {config.ANKI_SEEN_FILTER}"
    return query


def fetch_from_anki(deck: str = "Korean", *, anki: Optional[AnkiConnectClient] = None,
                    seen_only: bool = False) -> set[str]:
    """Every Korean surface form in `deck` and its subdecks.

    `seen_only` narrows it to cards that have actually come up in review — see
    `note_entries` for why the two readings are kept apart."""
    anki = anki or AnkiConnectClient()
    note_ids = anki.invoke("findNotes", query=deck_query(deck, seen_only)) or []
    forms: set[str] = set()
    for i in range(0, len(note_ids), _BATCH):
        for info in anki.invoke("notesInfo", notes=note_ids[i:i + _BATCH]) or []:
            forms |= _forms_from_note(info)
    return {f for f in forms if f}


def note_entries(deck: str = "Korean", *, anki: Optional[AnkiConnectClient] = None,
                 seen_only: bool = False) -> list[dict[str, Any]]:
    """Each note as its Korean forms plus the English side, for glossing.

    Same read as fetch_from_anki, but keeping the pairing: showing a word's meaning
    the way the learner's OWN deck words it beats a fresh translation, because it is
    the wording they have already been reviewing against.

    `seen_only` narrows it to cards that have actually come up in review. A card
    sitting unstudied in the new queue is not knowledge — colouring a word green
    because a card for it exists somewhere would make the overlay describe the
    collection rather than the learner."""
    anki = anki or AnkiConnectClient()
    note_ids = anki.invoke("findNotes", query=deck_query(deck, seen_only)) or []
    out: list[dict[str, Any]] = []
    for i in range(0, len(note_ids), _BATCH):
        for info in anki.invoke("notesInfo", notes=note_ids[i:i + _BATCH]) or []:
            forms = _forms_from_note(info)
            if forms:
                out.append({"forms": forms, "gloss": _english_from_note(info)})
    return out


def _english_from_note(info: dict[str, Any]) -> str:
    """The shortest field value that is not itself Korean — the gloss.

    Selected by content, not by field name, for the same reason the Korean side is:
    Back, Meaning, Definition and English all appear across the collection's note
    types, and a name-based map would silently return nothing for the next one."""
    best = ""
    for value in (info.get("fields") or {}).values():
        text = strip_field((value or {}).get("value", ""))
        if not text or len(text) > 200 or _is_korean_form(text) or _is_notation(text):
            continue
        if not best or len(text) < len(best):
            best = text
    return best


def _is_notation(text: str) -> bool:
    """Grammar notation like -(으)ㄹ까요? or 을/를, which is not a gloss.

    It slips past the Korean check because bare jamo (ㄹ) and brackets dilute the
    Hangul ratio, and it was then being shown as a word's meaning — the KBA pattern
    decks put the pattern in a field of its own, so 하다 was glossed "-(으)ㄹ까요?"
    rather than "do". English is the distinguishing feature: notation has none."""
    return len(_LATIN.findall(text)) < 2


def save_cache(forms: set[str], deck: str, seen_only: bool = False) -> None:
    p = cache_path(seen_only)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"deck": deck, "fetched_at": time.time(),
                             "forms": sorted(forms)}, ensure_ascii=False),
                 encoding="utf-8")


def load_cache(deck: str, seen_only: bool = False) -> Optional[set[str]]:
    p = cache_path(seen_only)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
    except ValueError:
        return None
    if data.get("deck") != deck:
        return None
    return set(data.get("forms") or [])


def known_surface_forms(deck: str = "Korean", *,
                        anki: Optional[AnkiConnectClient] = None,
                        seen_only: bool = False) -> set[str]:
    """Known vocabulary from Anki, falling back to the last successful read.

    A failure here must not stop a build: returning an empty set would silently
    re-teach everything, so a stale cache is strictly better than none."""
    try:
        forms = fetch_from_anki(deck, anki=anki, seen_only=seen_only)
        save_cache(forms, deck, seen_only)
        return forms
    except Exception:
        return load_cache(deck, seen_only) or set()
