"""Ingest a YouTube video: fetch (or load) its transcript, store cues, and
compute the segment/day plan. Fetch logic mirrors youtube-to-transcript/main.py
but writes to the DB instead of files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from sqlmodel import delete, select

from .. import db
from ..models import Segment, TranscriptCue, Video
from .segmenter import SegmentPlan, plan_segments, total_duration

_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def extract_video_id(value: str) -> str:
    """Accept a raw 11-char id or any common YouTube URL."""
    if _ID_RE.fullmatch(value):
        return value
    m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", value)
    if m:
        return m.group(1)
    raise ValueError(f"Could not extract a video id from: {value!r}")


def _cue_pairs(cues_raw: list[dict[str, Any]]) -> list[tuple[float, float]]:
    return [(float(c["start"]), float(c["start"]) + float(c["duration"])) for c in cues_raw]


def ingest_cues(
    youtube_id: str,
    cues_raw: list[dict[str, Any]],
    *,
    title: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Store a video + its cues + the computed segment plan. Returns a summary."""
    if not cues_raw:
        raise ValueError("transcript is empty")
    pairs = _cue_pairs(cues_raw)
    T = total_duration(pairs)
    plans = plan_segments(T, pairs)

    with db.session_scope() as s:
        existing = s.exec(select(Video).where(Video.youtube_id == youtube_id)).first()
        if existing:
            if not replace:
                raise ValueError(
                    f"video {youtube_id!r} already ingested (pass replace=True to re-ingest)"
                )
            vid = existing.id
            s.exec(delete(TranscriptCue).where(TranscriptCue.video_id == vid))
            s.exec(delete(Segment).where(Segment.video_id == vid))
            existing.title = title or existing.title
            existing.duration_s = T
            video = existing
        else:
            video = Video(youtube_id=youtube_id, title=title or youtube_id, duration_s=T)
            s.add(video)
        s.flush()  # assign video.id

        for i, c in enumerate(cues_raw):
            s.add(TranscriptCue(
                video_id=video.id, idx=i,
                start_s=float(c["start"]), dur_s=float(c["duration"]),
                text=str(c["text"]),
            ))
        for p in plans:
            s.add(Segment(
                video_id=video.id, index=p.index, day_number=p.day_number,
                start_s=p.start_s, end_s=p.end_s, is_full_review=p.is_full_review,
            ))
        s.flush()
        video_id = video.id

    n_study = sum(1 for p in plans if not p.is_full_review)
    return {
        "video_id": video_id,
        "youtube_id": youtube_id,
        "title": title or youtube_id,
        "duration_s": T,
        "n_cues": len(cues_raw),
        "n_study_segments": n_study,
        "n_days": len(plans),
    }


def ingest_from_file(
    path: str | Path, *, youtube_id: str | None = None,
    title: str | None = None, replace: bool = False,
) -> dict[str, Any]:
    """Ingest from a JSON file in youtube-transcript-api format
    (a list of {text, start, duration}). Filenames like `<id>_ko.json` yield the id."""
    p = Path(path)
    cues_raw = json.loads(p.read_text(encoding="utf-8"))
    if youtube_id is None:
        stem = p.stem
        youtube_id = stem.split("_")[0] if "_" in stem else stem
    return ingest_cues(youtube_id, cues_raw, title=title, replace=replace)


def ingest_from_youtube(
    url_or_id: str, *, languages: Optional[list[str]] = None,
    title: str | None = None, replace: bool = False,
) -> dict[str, Any]:
    """Fetch a transcript live via youtube-transcript-api and ingest it."""
    from youtube_transcript_api import YouTubeTranscriptApi  # lazy: network dep

    video_id = extract_video_id(url_or_id)
    langs = languages or ["ko"]
    api = YouTubeTranscriptApi()
    transcript = api.list(video_id).find_transcript(langs)
    fetched = transcript.fetch()
    cues_raw = [
        {"text": sn.text, "start": float(sn.start), "duration": float(sn.duration)}
        for sn in fetched
    ]
    return ingest_cues(video_id, cues_raw, title=title, replace=replace)


# --- read helpers (used by CLI / API / extraction) ----------------------

def list_videos() -> list[Video]:
    with db.session_scope() as s:
        return list(s.exec(select(Video).order_by(Video.created_at)).all())


def get_video(video_id: int) -> Optional[Video]:
    with db.session_scope() as s:
        return s.get(Video, video_id)


def get_segments(video_id: int) -> list[Segment]:
    with db.session_scope() as s:
        return list(s.exec(
            select(Segment).where(Segment.video_id == video_id).order_by(Segment.index)
        ).all())


def get_segment(segment_id: int) -> Optional[Segment]:
    with db.session_scope() as s:
        return s.get(Segment, segment_id)


def get_cues_in_range(video_id: int, start_s: float, end_s: float) -> list[TranscriptCue]:
    with db.session_scope() as s:
        return list(s.exec(
            select(TranscriptCue)
            .where(TranscriptCue.video_id == video_id)
            .where(TranscriptCue.start_s >= start_s)
            .where(TranscriptCue.start_s < end_s)
            .order_by(TranscriptCue.idx)
        ).all())


def segment_text(segment_id: int) -> str:
    """Joined transcript text for a segment (extraction input)."""
    seg = get_segment(segment_id)
    if seg is None:
        raise ValueError(f"no segment {segment_id}")
    cues = get_cues_in_range(seg.video_id, seg.start_s, seg.end_s)
    return " ".join(c.text.replace("\n", " ").strip() for c in cues).strip()
