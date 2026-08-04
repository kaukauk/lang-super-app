#!/usr/bin/env python3
"""Bulk-fetch a whole series from YouTube playlists, with a transcript per video.

The show is split into parts on YouTube — one video per `<episode>-<part>`, eight
or so parts to an episode — so a "season" is a playlist of ~150 short videos
rather than ~20 long ones. This walks a playlist, downloads each part, and runs
Whisper over it.

Layout. Videos land in the app's normal cache, `.media_cache/<youtube_id>/`, so
everything already built (ingest, clip cutting, AV cards) works on them without a
second code path. Human-readable names live alongside as a manifest and as
transcript copies under `.media_cache/series/s01/1-1.*`, because `dQw4w9WgXcQ`
tells you nothing about which part of which episode you are looking at.

These videos carry no YouTube captions, so the transcript has to come from
Whisper. That is the slow half by a wide margin — a download takes seconds, a
transcription tens of seconds — so downloads run in a small thread pool that
stays ahead of the single GPU-bound transcriber.

Resumable by design: every step checks for its own output first, so re-running
after a crash, a network drop, or a Ctrl-C costs only what was actually lost.

Usage:
    python -m scripts.fetch_series --season 1
    python -m scripts.fetch_series --season 1 2 3
    python -m scripts.fetch_series --season 1 --download-only
    python -m scripts.fetch_series --season 1 --limit 5      # smoke test
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                    # noqa: E402
from app.services import media, transcribe                # noqa: E402

PLAYLISTS = {
    1: "PLFrgA1YAzDZO6ocXxQ1pZ_2RBq1bTgQko",
    2: "PLFrgA1YAzDZNJ7hqAbGxJQdkEQxXtnPJc",
    3: "PLFrgA1YAzDZNHPWmQqV1W9sY5wYVqLQsS",
}

# The whole file is one segment: these parts are ~3 minutes, well under the length
# where the app splits a video into study-sized chunks.
SEGMENT_ID = 0

# Titles read "아앙의전설 1권 11-2" — series, volume, then episode-part. Anchor to the
# end so a stray number earlier in the title can't be mistaken for the episode.
_EP_PART = re.compile(r"(\d+)\s*-\s*(\d+)\s*$")


@dataclass
class Part:
    """One playlist entry: a single `<episode>-<part>` video."""
    season: int
    episode: int
    part: int
    youtube_id: str
    title: str
    index: int          # position in the playlist, for ordering fallbacks

    @property
    def name(self) -> str:
        return f"{self.episode}-{self.part}"

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.episode, self.part)


def series_dir(season: int) -> Path:
    return Path(config.MEDIA_CACHE_DIR) / "series" / f"s{season:02d}"


def manifest_path(season: int) -> Path:
    return series_dir(season) / "manifest.json"


# --- playlist listing ----------------------------------------------------

def list_playlist(season: int) -> list[Part]:
    """Flat-list a playlist and parse `<episode>-<part>` out of each title.

    `--flat-playlist` asks YouTube for the index only, so this is one request for
    the whole season rather than one per video."""
    url = f"https://www.youtube.com/playlist?list={PLAYLISTS[season]}"
    cmd = [*media._ytdlp_argv(), "--flat-playlist", "--no-progress",
           *media._js_runtime_args(),
           "--print", "%(playlist_index)s\t%(id)s\t%(title)s", url]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise media.MediaError(f"playlist listing failed: {(out.stderr or '')[-400:]}")

    parts: list[Part] = []
    unparsed: list[str] = []
    for line in out.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        idx, vid, title = fields[0], fields[1], "\t".join(fields[2:])
        m = _EP_PART.search(title)
        if not m:
            # Trailers, recaps and the occasional mis-titled upload. Skip rather
            # than guess: a wrong episode number is worse than a missing one, and
            # they are listed at the end so nothing goes quietly missing.
            unparsed.append(f"{idx} {vid} {title}")
            continue
        parts.append(Part(season=season, episode=int(m.group(1)), part=int(m.group(2)),
                          youtube_id=vid, title=title, index=int(idx or 0)))

    if unparsed:
        print(f"  [skip] {len(unparsed)} entries with no <episode>-<part> in the title:")
        for u in unparsed:
            print(f"         {u}")
    parts.sort(key=lambda p: p.sort_key)
    return parts


def save_manifest(season: int, parts: list[Part]) -> Path:
    p = manifest_path(season)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([asdict(x) for x in parts], ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def load_manifest(season: int) -> Optional[list[Part]]:
    p = manifest_path(season)
    if not p.exists():
        return None
    return [Part(**d) for d in json.loads(p.read_text("utf-8"))]


# --- per-part work -------------------------------------------------------

def video_path(part: Part) -> Optional[Path]:
    """The downloaded file, if it exists. yt-dlp may pick a non-mp4 container."""
    d = media.cache_dir(part.youtube_id)
    if not d.exists():
        return None
    return next((p for p in sorted(d.glob("source.*")) if p.suffix != ".part"), None)


def download(part: Part) -> Path:
    if (existing := video_path(part)) is not None:
        return existing
    paths = media.ensure_video(part.youtube_id,
                               url=f"https://www.youtube.com/watch?v={part.youtube_id}")
    return paths.video


def transcript_paths(part: Part) -> dict[str, Path]:
    d = series_dir(part.season)
    return {ext: d / f"{part.name}.{ext}" for ext in ("txt", "srt", "json")}


def transcribe_part(part: Part, transcriber: transcribe.Transcriber) -> list[transcribe.Sentence]:
    """Whisper the whole video, caching the word stream where the app expects it.

    Audio is extracted to 16 kHz mono WAV first — Whisper resamples to that anyway,
    and handing it a prepared file keeps the decode out of the timed section."""
    cached = transcribe.load_cached(part.youtube_id, SEGMENT_ID)
    if cached:
        return cached

    src = video_path(part) or download(part)
    seg_dir = media.segment_dir(part.youtube_id, SEGMENT_ID)
    seg_dir.mkdir(parents=True, exist_ok=True)
    wav = seg_dir / "audio.wav"
    if not wav.exists():
        media.extract_audio(src, 0.0, media.probe_duration(src), wav,
                            fmt="wav", sample_rate=16000, mono=True)

    sentences = transcriber.transcribe(wav)
    transcribe.save_cache(part.youtube_id, SEGMENT_ID, sentences)
    wav.unlink(missing_ok=True)      # ~5 MB each, and trivially re-extracted
    return sentences


# --- transcript output ---------------------------------------------------

def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(sentences: list[transcribe.Sentence]) -> str:
    blocks = []
    for i, s in enumerate(sentences, 1):
        blocks.append(f"{i}\n{_srt_time(s.start_s)} --> {_srt_time(s.end_s)}\n"
                      f"{s.text.strip()}\n")
    return "\n".join(blocks)


def write_transcripts(part: Part, sentences: list[transcribe.Sentence]) -> None:
    """Three formats, because each answers a different question: plain text to
    read, SRT to watch along with, JSON to feed back into the pipeline."""
    out = transcript_paths(part)
    out["txt"].parent.mkdir(parents=True, exist_ok=True)
    out["txt"].write_text("\n".join(s.text.strip() for s in sentences) + "\n",
                          encoding="utf-8")
    out["srt"].write_text(to_srt(sentences), encoding="utf-8")
    out["json"].write_text(
        json.dumps({"season": part.season, "episode": part.episode, "part": part.part,
                    "youtube_id": part.youtube_id, "title": part.title,
                    "sentences": [s.as_dict() for s in sentences]},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def is_done(part: Part) -> bool:
    return all(p.exists() for p in transcript_paths(part).values())


# --- driver --------------------------------------------------------------

def prefetch(parts: list[Part], workers: int) -> Iterator[Part]:
    """Yield parts in order, downloading ahead of the consumer.

    Transcription is the bottleneck and holds the GPU, so downloads run on their
    own threads and stay a few videos ahead. The queue is bounded: without a cap,
    a fast connection would pull the entire season to disk while Whisper is still
    on video three."""
    q: queue.Queue = queue.Queue(maxsize=workers * 2)
    todo: queue.Queue = queue.Queue()
    for p in parts:
        todo.put(p)
    results: dict[str, object] = {}
    lock = threading.Lock()
    done = threading.Event()

    def worker():
        while not done.is_set():
            try:
                part = todo.get_nowait()
            except queue.Empty:
                return
            try:
                download(part)
                outcome: object = True
            except Exception as exc:                      # noqa: BLE001
                outcome = exc
            with lock:
                results[part.youtube_id] = outcome
            q.put(part.youtube_id)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()

    try:
        # Consume in playlist order, not completion order: an out-of-order
        # transcript would be confusing to watch being written.
        for part in parts:
            while True:
                with lock:
                    outcome = results.get(part.youtube_id)
                if outcome is not None:
                    break
                q.get()
            if isinstance(outcome, Exception):
                print(f"  [fail] {part.name} download: {outcome}")
                continue
            yield part
    finally:
        done.set()


def run_season(season: int, *, limit: Optional[int], download_only: bool,
               workers: int, refresh: bool, fake: bool) -> dict[str, int]:
    print(f"\n=== Season {season} ===")
    parts = None if refresh else load_manifest(season)
    if parts is None:
        print("  listing playlist…")
        parts = list_playlist(season)
        save_manifest(season, parts)
    print(f"  {len(parts)} parts, episodes {parts[0].name} … {parts[-1].name}"
          if parts else "  (empty playlist)")
    if limit:
        parts = parts[:limit]

    pending = [p for p in parts if not is_done(p)]
    if len(pending) < len(parts):
        print(f"  {len(parts) - len(pending)} already done, {len(pending)} to go")
    if not pending:
        return {"done": len(parts), "failed": 0}

    transcriber: transcribe.Transcriber = (
        transcribe.FakeTranscriber() if fake else transcribe.WhisperTranscriber())

    stats = {"done": len(parts) - len(pending), "failed": 0}
    started = time.monotonic()
    for i, part in enumerate(prefetch(pending, workers), 1):
        if download_only:
            print(f"  [{i}/{len(pending)}] {part.name} downloaded")
            stats["done"] += 1
            continue
        try:
            sentences = transcribe_part(part, transcriber)
            write_transcripts(part, sentences)
        except Exception as exc:                          # noqa: BLE001
            print(f"  [fail] {part.name}: {type(exc).__name__}: {exc}")
            stats["failed"] += 1
            continue
        stats["done"] += 1
        rate = (time.monotonic() - started) / i
        left = int(rate * (len(pending) - i))
        print(f"  [{i}/{len(pending)}] {part.name}  {len(sentences)} sentences"
              f"  (~{left // 60}m{left % 60:02d}s left)", flush=True)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, nargs="+", default=[1], choices=sorted(PLAYLISTS))
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N parts of each season (smoke test)")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the videos, skip transcription")
    ap.add_argument("--workers", type=int, default=3, help="parallel downloads")
    ap.add_argument("--refresh", action="store_true",
                    help="re-list the playlists instead of using the saved manifest")
    ap.add_argument("--fake", action="store_true",
                    help="canned transcripts, for exercising the plumbing offline")
    args = ap.parse_args()

    totals = {"done": 0, "failed": 0}
    for season in args.season:
        s = run_season(season, limit=args.limit, download_only=args.download_only,
                       workers=args.workers, refresh=args.refresh, fake=args.fake)
        for k in totals:
            totals[k] += s[k]
        print(f"  season {season}: {s['done']} done, {s['failed']} failed")

    print(f"\ntotal: {totals['done']} done, {totals['failed']} failed")
    print(f"transcripts under {Path(config.MEDIA_CACHE_DIR) / 'series'}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
