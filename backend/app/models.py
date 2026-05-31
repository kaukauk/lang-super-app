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


class SparSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    segment_id: Optional[int] = Field(default=None, foreign_key="segment.id")
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
