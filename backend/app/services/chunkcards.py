"""Push harvested sparring chunks to Anki as Korean-only production cards.

The card design and the reasoning for one-card-per-chunk live in anki/chunks.py.
This module is the plumbing: TTS the chunk, upload the audio, add the note.

Kept separate from avcards.py because nothing is shared — no video, no Whisper, no
segment. A harvest is a handful of chunks from a conversation that just happened.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .. import config
from ..anki.chunks import ChunkNote, build_model_payload, build_note_payload
from ..anki.connect import AnkiConnectClient
from . import media, tts
from .extraction import Chunk


def _correction_for(chunk_ko: str, corrections: list[dict]) -> str:
    """The learner's original wording for this chunk, if it came from a correction.

    Matched on the corrected text so the blank can land on the span that actually
    failed. Substring either way: the harvest often trims a correction down to the
    reusable part, or extends it into a fuller phrase."""
    for c in corrections or []:
        corrected = str(c.get("corrected", "")).strip()
        original = str(c.get("original", "")).strip()
        if not corrected or not original:
            continue
        if corrected == chunk_ko or corrected in chunk_ko or chunk_ko in corrected:
            return original
    return ""


def _context_for(chunk_ko: str, history: list[dict]) -> str:
    """The assistant line the learner was answering when this chunk came up.

    Cues the card from meaning instead of from a Korean frame the learner will never
    have when speaking — the main weakness of a bare cloze. Korean, so retrieval
    stays inside the language."""
    last_assistant = ""
    for turn in history or []:
        text = (turn.get("text") or "").strip()
        if turn.get("role") == "assistant":
            last_assistant = text
        elif turn.get("role") == "user" and text and chunk_ko.strip() in text:
            return last_assistant
    return ""


def build_chunk_notes(
    chunks: list[Chunk], *, corrections: Optional[list[dict]] = None,
    history: Optional[list[dict]] = None, tags: Optional[list[str]] = None,
) -> list[ChunkNote]:
    return [
        ChunkNote(
            chunk_ko=c.ko, ending=c.ending, anchor=c.anchor,
            corrected_from=_correction_for(c.ko, corrections or []),
            context_ko=_context_for(c.ko, history or []),
            tags=list(tags or ["korean-chunk"]),
        )
        for c in chunks if c.ko.strip()
    ]


def notes_from_lookups(lookups: list[dict], *, tags: Optional[list[str]] = None,
                       ) -> list[ChunkNote]:
    """Cards from words the learner clicked mid-sparring.

    The clicked word is the blank and the sentence it appeared in is the chunk — the
    question already carries its own context, so nothing extra is needed. These keep
    the English gloss, unlike every other card here: a click is a COMPREHENSION gap,
    and a cloze over a word whose meaning you learned seconds ago can be completed by
    pattern-matching the Korean frame without the meaning ever sticking. The gloss is
    back-only, so it is never a translation prompt."""
    out: list[ChunkNote] = []
    for lk in lookups or []:
        word = str(lk.get("word", "")).strip()
        context = str(lk.get("context_ko", "")).strip()
        if not word or not context or word == context:
            continue
        out.append(ChunkNote(
            chunk_ko=context, blank_on=word,
            ending=str(lk.get("ending", "")).strip(),
            gloss_en=str(lk.get("gloss_en", "")).strip(),
            tags=list(tags or ["korean-chunk", "lookup"]),
        ))
    return out


def browse(deck: Optional[str] = None, *, anki: Optional[AnkiConnectClient] = None,
           ) -> dict:
    """Every harvested chunk card, read live from Anki.

    Anki is the source of truth rather than a local mirror: it is where these are
    actually reviewed, so an edit, suspend or delete there must show here
    immediately. The cost is that this needs Anki running — the caller surfaces that
    rather than silently showing a stale list."""
    deck = deck or config.ANKI_CHUNK_DECK
    anki = anki or AnkiConnectClient()
    note_ids = anki.invoke("findNotes", query=f'deck:"{deck}"') or []
    if not note_ids:
        return {"deck": deck, "cards": []}
    cards = []
    for info in anki.invoke("notesInfo", notes=note_ids) or []:
        f = {k: v.get("value", "") for k, v in (info.get("fields") or {}).items()}
        cards.append({
            "note_id": info.get("noteId"),
            "prompt": f.get("Prompt", ""),
            "chunk": f.get("Chunk", ""),
            "context": f.get("Context", ""),
            "ending": f.get("Ending", ""),
            "anchor": f.get("Anchor", ""),
            "target": f.get("Target", ""),
            "gloss": f.get("Gloss", ""),
            # strip the [sound:...] wrapper — the browser needs a bare filename
            "audio": re.sub(r"^\[sound:(.*)\]$", r"\1", f.get("Audio", "").strip()),
            "tags": info.get("tags") or [],
        })
    return {"deck": deck, "cards": cards}


def push_chunks(
    chunks: list[Chunk], *, corrections: Optional[list[dict]] = None,
    history: Optional[list[dict]] = None, lookups: Optional[list[dict]] = None,
    anki: Optional[AnkiConnectClient] = None,
    tts_provider: Optional[tts.TTS] = None,
    deck: Optional[str] = None, model: Optional[str] = None,
    session_id: Optional[int] = None, make_audio: bool = True,
) -> dict:
    """Create the deck/note type if needed, then add one note per chunk.

    Duplicates are skipped rather than raising, so re-running a harvest is a no-op
    instead of a crash — same contract as the AV push."""
    deck = deck or config.ANKI_CHUNK_DECK
    model = model or config.ANKI_CHUNK_MODEL
    anki = anki or AnkiConnectClient()

    tags = ["korean-chunk"] + ([f"spar{session_id}"] if session_id is not None else [])
    notes = (build_chunk_notes(chunks, corrections=corrections, history=history,
                               tags=tags)
             + notes_from_lookups(lookups, tags=tags + ["lookup"]))
    if not notes:
        return {"pushed": 0, "skipped": 0, "notes": []}

    anki.ensure_deck(deck)
    anki.ensure_model(model, payload=build_model_payload(model))

    out_dir = Path(config.MEDIA_CACHE_DIR) / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = tts_provider or tts.EdgeTTS()

    pushed, skipped, rows = 0, 0, []
    for note in notes:
        if make_audio:
            # The card has no English, so the audio is the only non-textual anchor
            # on the answer. A TTS failure must not cost the card.
            try:
                path = tts.synth_cached(note.chunk_ko, out_dir, provider=provider,
                                        prefix="chunk")
                note.audio_file = Path(path).name
                anki.store_media_file(note.audio_file, path)
            except Exception:
                note.audio_file = ""
        note_id = anki.add_note_payload(build_note_payload(note, deck=deck, model=model))
        if note_id:
            pushed += 1
        else:
            skipped += 1
        rows.append({**note.fields(), "anki_note_id": note_id})
    return {"pushed": pushed, "skipped": skipped, "deck": deck, "notes": rows}
