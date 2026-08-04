"""SQLModel tables. SRS scheduling fields live on Card but are *internal only* —
they are never written to / read from the Anki TXT import-export."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Video(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    youtube_id: str = Field(index=True, unique=True)
    title: str = ""
    duration_s: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)


class TranscriptCue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(index=True, foreign_key="video.id")
    idx: int                       # position within the video's cue list
    start_s: float
    dur_s: float
    text: str

    @property
    def end_s(self) -> float:
        return self.start_s + self.dur_s


class Segment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(index=True, foreign_key="video.id")
    index: int                     # 0-based order
    day_number: int                # 1-based study day
    start_s: float
    end_s: float
    is_full_review: bool = False    # the final full-rewatch day


class Deck(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(default="Korean", unique=True)


class Card(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    deck_id: int = Field(index=True, foreign_key="deck.id")
    kind: str = "sentence"          # sentence | noun_verb | grammar | manual | subtitle
    # --- content (the only thing that round-trips with Anki) ---
    front_ko: str
    back_en: str = ""
    gloss: str = ""
    surface_forms: str = "[]"       # JSON list[str] of lemmas for dedupe / targeting
    source_segment_id: Optional[int] = Field(default=None, foreign_key="segment.id")
    # --- SRS / SM-2 state (internal only, day-ordinal based for determinism) ---
    state: str = "new"              # new | review
    interval: int = 0               # days
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0
    due_ord: Optional[int] = Field(default=None, index=True)  # date.toordinal()
    last_reviewed_ord: Optional[int] = None
    created_at: datetime = Field(default_factory=_utcnow)


class ExtractionRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    segment_id: int = Field(index=True, foreign_key="segment.id")
    primer_md: str = ""
    raw_json: str = ""              # full LLM payload for audit
    created_at: datetime = Field(default_factory=_utcnow)


class AvCard(SQLModel, table=True):
    """An audio/video card built from the video (Whisper-timed) and pushed to Anki.
    Separate from the text-`Card`/SRS flow — Anki owns the scheduling for these."""
    id: Optional[int] = Field(default=None, primary_key=True)
    segment_id: int = Field(index=True, foreign_key="segment.id")
    target_idx: int                 # position among the segment's targets (0-based)
    target_ko: str                  # the target sentence (front + dedupe key)
    context_ko: str = ""            # the preceding sentence (back context)
    explanation_en: str = ""        # brief, context-oriented English (back)
    start_s: float = 0.0            # target sentence span (front audio)
    end_s: float = 0.0
    context_start_s: float = 0.0    # clip span start (= preceding sentence start)
    audio_file: str = ""            # front original-audio filename (in collection.media)
    clip_file: str = ""             # back video-clip filename
    primer_audio_file: str = ""     # in-app primer's clean edge-tts audio (NOT in Anki)
    anki_note_id: Optional[int] = None
    # Primer check, run between the blind watch and the study watch: did the learner
    # catch this line? None = not checked yet, so "unseen" and "not yet asked" stay
    # distinct and a half-finished check can be resumed rather than restarted.
    seen: Optional[bool] = None
    created_at: datetime = Field(default_factory=_utcnow)


class SparSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    segment_id: Optional[int] = Field(default=None, foreign_key="segment.id")
    role_en: str = ""               # the character the learner was assigned (State 2)
    scene_en: str = ""              # short English scene description for continuity
    started_at: datetime = Field(default_factory=_utcnow)


class SparTurn(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True, foreign_key="sparsession.id")
    role: str                       # user | assistant
    text_ko: str = ""
    gloss_json: str = "{}"
    feedback_json: str = "{}"
    targets_used_json: str = "[]"
    created_at: datetime = Field(default_factory=_utcnow)
