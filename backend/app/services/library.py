"""The immersion library: series of studyable videos, and where you are in one.

Joined episodes live on disk as `<cache>/series/sNN/episodes/<stem>.{mp4,json}`.
This turns them into Video rows with the same shape as YouTube ingests, so the
whole downstream pipeline — segment planning, primers, AV cards, sparring — works
on them without a second code path.

Progress is a row per finished segment rather than a cursor per series. Days can
be studied out of order, and a cursor would quietly lose that; "where was I" is
the first segment with no row, which stays right however you move around.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from .. import config, db
from ..models import Segment, SegmentProgress, Video
from . import ingest

# The joined-episode stem: "3" for a whole episode, "8a" when a missing part split
# it. The letter sorts partial episodes after nothing and before the next number.
_STEM = re.compile(r"^(\d+)([a-z]?)$")

SERIES_TITLE = "아앙의전설 (Avatar: The Last Airbender)"


def episodes_dir(season: int) -> Path:
    return Path(config.MEDIA_CACHE_DIR) / "series" / f"s{season:02d}" / "episodes"


def local_key(series_slug: str, season: int, stem: str) -> str:
    """Identity for a local video.

    Reuses `youtube_id` deliberately: media paths, clip cutting and AV cards all
    resolve by that column already, so a synthetic key keeps one code path instead
    of a nullable second identifier and a branch at every call site."""
    return f"local:{series_slug}-s{season:02d}e{stem}"


def sort_key(season: int, episode: int, suffix: str) -> str:
    """Zero-padded so string ordering matches viewing order: s01e08a < s01e08b
    < s01e09. Sorting on the raw stem would put episode 10 before episode 9."""
    return f"s{season:02d}e{episode:03d}{suffix or ' '}"


def scan_episodes(season: int) -> list[dict[str, Any]]:
    """Every joined episode on disk for a season, in viewing order."""
    d = episodes_dir(season)
    if not d.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for meta_path in d.glob("*.json"):
        m = _STEM.match(meta_path.stem)
        video = meta_path.with_suffix(".mp4")
        if not m or not video.exists():
            continue
        meta = json.loads(meta_path.read_text("utf-8"))
        episode, suffix = int(m.group(1)), m.group(2)
        found.append({
            "stem": meta_path.stem, "episode": episode, "suffix": suffix,
            "season": meta.get("season", season), "video": video, "meta": meta,
            "sort_key": sort_key(season, episode, suffix),
        })
    return sorted(found, key=lambda e: e["sort_key"])


def _cues_from_sentences(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whisper/caption sentences in the shape the cue table expects.

    Durations are clamped to a positive minimum: a zero-length cue contributes no
    boundary candidate, and the segment planner snaps day boundaries to cue gaps."""
    out = []
    for s in sentences:
        start = float(s.get("start_s", 0.0))
        end = float(s.get("end_s", start))
        text = (s.get("text") or "").strip()
        if not text:
            continue
        out.append({"text": text, "start": start, "duration": max(0.2, end - start)})
    return out


def ingest_season(season: int, *, series_slug: str = "atla",
                  title: str = SERIES_TITLE, replace: bool = False) -> dict[str, Any]:
    """Add (or refresh) every joined episode of a season to the library."""
    added, skipped, failed = [], [], []
    for ep in scan_episodes(season):
        key = local_key(series_slug, season, ep["stem"])
        with db.session_scope() as s:
            exists = s.exec(select(Video).where(Video.youtube_id == key)).first()
        if exists and not replace:
            skipped.append(ep["stem"])
            continue

        cues = _cues_from_sentences(ep["meta"].get("sentences") or [])
        if not cues:
            # No transcript means no segment plan, no primer and no cards. Better
            # to leave it out of the library than to show a day that cannot be run.
            failed.append(f"{ep['stem']} (no transcript)")
            continue

        parts = ep["meta"].get("parts") or []
        label = (f"{ep['stem']} (parts {ep['meta'].get('first_part')}"
                 f"-{ep['meta'].get('last_part')})") if ep["suffix"] else None
        name = f"S{season}E{ep['episode']}" + (f"{ep['suffix']}" if ep["suffix"] else "")
        try:
            summary = ingest.ingest_cues(
                key, cues, title=f"{title} — {name}", replace=True,
                meta={
                    "source": "local",
                    "local_path": str(ep["video"].resolve()),
                    "series": title,
                    "season": season,
                    "episode": ep["episode"],
                    "sort_key": ep["sort_key"],
                    "part_label": label,
                },
            )
        except Exception as exc:                          # noqa: BLE001
            failed.append(f"{ep['stem']} ({type(exc).__name__}: {exc})")
            continue
        summary["n_parts"] = len(parts)
        added.append(summary)

    return {"season": season, "added": len(added), "skipped": skipped,
            "failed": failed, "episodes": added}


# --- progress ------------------------------------------------------------

def mark_segment_done(segment_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            raise ValueError(f"no segment {segment_id}")
        row = s.exec(select(SegmentProgress)
                     .where(SegmentProgress.segment_id == segment_id)).first()
        if row is None:
            s.add(SegmentProgress(segment_id=segment_id, video_id=seg.video_id))
            s.flush()
        return {"segment_id": segment_id, "video_id": seg.video_id, "done": True}


def unmark_segment(segment_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        row = s.exec(select(SegmentProgress)
                     .where(SegmentProgress.segment_id == segment_id)).first()
        if row is not None:
            s.delete(row)
        return {"segment_id": segment_id, "done": False}


def done_segment_ids() -> set[int]:
    with db.session_scope() as s:
        return {r.segment_id for r in s.exec(select(SegmentProgress)).all()}


def _video_rows(series: Optional[str] = None) -> list[Video]:
    with db.session_scope() as s:
        q = select(Video)
        if series:
            q = q.where(Video.series == series)
        rows = list(s.exec(q).all())
    # Series entries sort by episode; loose videos fall back to insertion order.
    return sorted(rows, key=lambda v: (v.sort_key or "~", v.created_at))


def overview() -> dict[str, Any]:
    """Everything the library screen needs, in one call.

    One query set rather than a call per video: the shelf shows every episode with
    its progress, and doing that per-row would be dozens of round trips for a
    screen that is meant to feel instant."""
    done = done_segment_ids()
    with db.session_scope() as s:
        segments = list(s.exec(select(Segment).order_by(Segment.video_id,
                                                        Segment.index)).all())
    by_video: dict[int, list[Segment]] = {}
    for seg in segments:
        by_video.setdefault(seg.video_id, []).append(seg)

    series: dict[str, dict[str, Any]] = {}
    loose: list[dict[str, Any]] = []
    for v in _video_rows():
        segs = by_video.get(v.id or -1, [])
        n_done = sum(1 for seg in segs if seg.id in done)
        nxt = next((seg for seg in segs if seg.id not in done), None)
        entry = {
            "video_id": v.id, "youtube_id": v.youtube_id, "title": v.title,
            "source": v.source, "season": v.season, "episode": v.episode,
            "part_label": v.part_label, "duration_s": v.duration_s,
            "n_days": len(segs), "n_done": n_done,
            "complete": bool(segs) and n_done == len(segs),
            "next_segment_id": nxt.id if nxt else None,
            "next_day": nxt.day_number if nxt else None,
        }
        if v.series:
            sr = series.setdefault(v.series, {"title": v.series, "seasons": {}})
            sr["seasons"].setdefault(v.season or 0, []).append(entry)
        else:
            loose.append(entry)

    out_series = []
    for sr in series.values():
        seasons = [{"season": k, "episodes": v} for k, v in sorted(sr["seasons"].items())]
        eps = [e for s_ in seasons for e in s_["episodes"]]
        # "Continue" points at the first unfinished day in viewing order, which is
        # what one-click resume needs — not the most recently touched episode,
        # since finishing episode 5 should offer 6, not 5 again.
        nxt = next((e for e in eps if not e["complete"] and e["next_segment_id"]), None)
        out_series.append({
            "title": sr["title"], "seasons": seasons,
            "n_episodes": len(eps),
            "n_complete": sum(1 for e in eps if e["complete"]),
            "days_total": sum(e["n_days"] for e in eps),
            "days_done": sum(e["n_done"] for e in eps),
            "continue": nxt,
        })
    return {"series": out_series, "videos": loose}
