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

Transcripts come from YouTube's Korean auto-captions by default — about two
seconds a part against Whisper's forty-five, which is the difference between
twenty minutes and five hours over a whole series. They carry per-word timings,
so clip cutting still works; what they lack is punctuation, which the sentence
grouper would rather have. `--whisper` transcribes locally instead, and is worth
spending on the parts you actually build cards from.

Downloads run in a small thread pool, which matters most under --whisper: there
the GPU is the bottleneck and the pool keeps it fed.

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
import random
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


# YouTube's ways of saying "you are a bot". They escalate: plain 403s first, then
# demands for visitor data or sign-in once it has made up its mind.
#
# Match only definitive phrasing. A bare "cookies" seemed reasonable and was not:
# yt-dlp appends a "see how to pass cookies" HINT to unrelated messages, so a part
# that merely lacked captions was read as throttled, and the run sat doubling a
# cooldown against a video that was never coming.
_BLOCK_SIGNS = ("visitor data", "not a bot", "sign in to confirm",
                "http error 429", "http error 403")

# The unambiguous "this video has no such track" answer. Checked first, because it
# is a fact about the video and outranks any guess about our connection.
_NO_TRACK = "no subtitles for the requested languages"

# Videos that are not coming back however long we wait: taken down on a rights
# claim, made private, or deleted. Retrying is pure waste -- four attempts with
# backoff each, per part, for something no amount of patience fixes -- and worse,
# it reads as flakiness when it is really a permanent hole in the playlist.
_GONE_SIGNS = ("video unavailable", "blocked due to", "private video",
               "removed by the uploader", "account associated with this video "
               "has been terminated", "video has been removed")


class Gate:
    """A shared cooldown, because throttling is a property of the run, not the part.

    When YouTube starts refusing, it refuses everything — so retrying one part on
    its own backoff means the other workers march straight into the same wall and
    the run burns through a season failing three seconds at a time. That is exactly
    how parts 12-3 through 12-6 were lost: four attempts each, all inside the same
    blocked minute, all doomed before they started.

    So a block pauses every worker, and the cooldown doubles while blocks keep
    coming. A clean download resets it, so one bad patch doesn't slow the rest of
    the run forever."""

    def __init__(self, *, base_s: float = 60.0, max_s: float = 900.0):
        self._lock = threading.Lock()
        self._until = 0.0
        self._streak = 0
        self._base, self._max = base_s, max_s

    @staticmethod
    def is_block(exc: BaseException) -> bool:
        return any(s in str(exc).lower() for s in _BLOCK_SIGNS)

    def wait(self) -> None:
        while True:
            with self._lock:
                left = self._until - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 5.0))

    def penalise(self) -> float:
        with self._lock:
            self._streak += 1
            cool = min(self._base * (2 ** (self._streak - 1)), self._max)
            self._until = max(self._until, time.monotonic() + cool)
            return cool

    def clear(self) -> None:
        with self._lock:
            self._streak = 0


GATE = Gate()


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

    # Titles read "<series> N권 <episode>-<part>", so the volume is stated in every
    # title. Cross-check it against the playlist we think we're on: if a playlist id
    # ever points somewhere unexpected, this catches it here rather than after 150
    # videos have been filed under the wrong season.
    volumes = {int(m.group(1)) for p in parts
               if (m := re.search(r"(\d+)\s*권", p.title))}
    if volumes and volumes != {season}:
        print(f"  [warn] season {season} playlist has volume(s) {sorted(volumes)} "
              f"in its titles — check the playlist id")

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


def download(part: Part, *, attempts: int = 4) -> Path:
    """Fetch one part, retrying on the transient failures a 400-video run will hit.

    YouTube rate-limits a few requests into a run and answers 403; the same URL
    downloads fine seconds later. Without a retry a single blip skips that part
    permanently, and you don't find out until you sit down to watch episode 1 and
    it's missing its fourth minute. Backoff is exponential and jittered so
    concurrent workers that all get throttled at once don't march back in step."""
    last: Optional[Exception] = None
    for attempt in range(attempts):
        if (existing := video_path(part)) is not None:
            return existing
        GATE.wait()
        try:
            video = media.ensure_video(
                part.youtube_id,
                url=f"https://www.youtube.com/watch?v={part.youtube_id}").video
            GATE.clear()
            return video
        except Exception as exc:                          # noqa: BLE001
            last = exc
            # A half-written source.* would otherwise be mistaken for a good file.
            for stray in media.cache_dir(part.youtube_id).glob("source.*"):
                stray.unlink(missing_ok=True)
            if any(s in str(exc).lower() for s in _GONE_SIGNS):
                raise media.MediaError(f"{part.name}: gone from YouTube: "
                                       f"{str(exc)[-160:]}") from exc
            if attempt == attempts - 1:
                break
            if Gate.is_block(exc):
                cool = GATE.penalise()
                print(f"  [throttled] pausing {cool:.0f}s — YouTube is refusing "
                      f"({part.name})", flush=True)
            else:
                delay = 2.0 * (2 ** attempt) + random.uniform(0, 1.5)
                print(f"  [retry] {part.name} in {delay:.0f}s ({str(exc)[-90:]})",
                      flush=True)
                time.sleep(delay)
    raise media.MediaError(f"{part.name}: {attempts} attempts failed: {last}")


def caption_words(part: Part) -> list[transcribe.Word]:
    """YouTube's own Korean auto-captions, as a word stream.

    Twenty times faster than running Whisper and close in coverage — on the part
    measured, 708 transcribed characters against Whisper's 704. json3 is the format
    worth asking for: unlike vtt it carries a per-word offset inside each caption
    event, and per-word timing is what the clip cutter needs.

    What it does NOT carry is punctuation, not one mark against Whisper's 37. That
    matters because sentence grouping looks for terminal punctuation first; here it
    falls through to the Korean-ender-plus-pause rule, which is a weaker signal.
    Hence --whisper: captions to get the series usable, Whisper for the parts you
    actually build cards from."""
    d = media.cache_dir(part.youtube_id)
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "captions.ko.json3"
    # Caption fetches get 403'd by the same rate limiting as video downloads, and a
    # throttled request looks exactly like a video that has no captions. Retry
    # before concluding the track is missing, or a busy moment silently downgrades
    # a part to Whisper.
    for attempt in range(3):
        if raw.exists():
            break
        GATE.wait()
        cmd = [*media._ytdlp_argv(), "--skip-download", "--write-auto-subs",
               "--sub-langs", "ko", "--sub-format", "json3", "--no-progress",
               *media._js_runtime_args(), *media.cookie_args(),
               "-o", str(d / "captions.%(ext)s"),
               f"https://www.youtube.com/watch?v={part.youtube_id}"]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if raw.exists():
            GATE.clear()
            break
        # Both streams matter: yt-dlp reports a missing track on stdout as [info]
        # and leaves stderr empty, but reports a block on stderr.
        err = "\n".join(x.strip() for x in (out.stderr, out.stdout) if x).strip()
        low = err.lower()
        if _NO_TRACK in low:
            raise media.MediaError("no Korean auto-captions: none published")
        blocked = any(s in low for s in _BLOCK_SIGNS)
        if attempt == 2:
            # Distinguish the two, because they call for different answers: a video
            # with no caption track needs Whisper, a throttled one just needs time.
            raise media.MediaError(
                ("throttled, not missing: " if blocked else "no Korean auto-captions: ")
                + err[-200:])
        if blocked:
            cool = GATE.penalise()
            print(f"  [throttled] pausing {cool:.0f}s — captions refused "
                  f"({part.name})", flush=True)
        else:
            time.sleep(2.0 * (2 ** attempt) + random.uniform(0, 1.5))

    events = json.loads(raw.read_text("utf-8")).get("events") or []
    words: list[transcribe.Word] = []
    for ev in events:
        base = ev.get("tStartMs", 0)
        for seg in ev.get("segs") or []:
            text = seg.get("utf8", "")
            if not text.strip():
                continue
            start = (base + seg.get("tOffsetMs", 0)) / 1000.0
            # json3 gives a start per word but no end; the next word's start is the
            # best available bound, and the last word gets the event's duration.
            words.append(transcribe.Word(text=text, start_s=start, end_s=start))
    for i, w in enumerate(words[:-1]):
        w.end_s = max(w.start_s, words[i + 1].start_s)
    if words:
        last_ms = events[-1].get("tStartMs", 0) + events[-1].get("dDurationMs", 2000)
        words[-1].end_s = max(words[-1].start_s, last_ms / 1000.0)
    return words


_WHISPER: Optional[transcribe.Transcriber] = None


def whisper_fallback() -> transcribe.Transcriber:
    """Whisper, built on first use only. The model is a heavy load, and a
    captions-only run must not pay for it just in case."""
    global _WHISPER
    if _WHISPER is None:
        _WHISPER = transcribe.WhisperTranscriber()
    return _WHISPER


def transcribe_from_captions(part: Part) -> list[transcribe.Sentence]:
    cached = transcribe.load_cached(part.youtube_id, SEGMENT_ID)
    if cached:
        return cached
    sentences = transcribe.group_sentences(caption_words(part))
    transcribe.save_cache(part.youtube_id, SEGMENT_ID, sentences)
    return sentences


def transcript_paths(part: Part) -> dict[str, Path]:
    d = series_dir(part.season)
    return {ext: d / f"{part.name}.{ext}" for ext in ("txt", "srt", "json")}


def link_video(part: Part) -> Optional[Path]:
    """Name the downloaded file `s01/1-4.mp4` alongside its transcript.

    Keeping the videos is the point of this script, and `.media_cache/mcyw2G9laVg/
    source.mp4` tells you nothing about which part of which episode it is. A symlink
    rather than a copy or a move: the canonical path is what the rest of the app
    resolves by youtube id, so duplicating four gigabytes to rename it would be
    wasteful and moving it would break every other caller."""
    src = video_path(part)
    if src is None:
        return None
    link = series_dir(part.season) / f"{part.name}{src.suffix}"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.resolve() == src.resolve():
            return link
        link.unlink()
    elif link.exists():
        return link          # a real file someone put there; leave it alone
    link.symlink_to(src)
    return link


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
    """Done means the video is on disk AND the transcript is written.

    The video is checked too, not just the transcript: keeping the videos is the
    point, so a part whose file went missing should be re-fetched rather than
    counted as complete on the strength of a transcript it left behind."""
    return (video_path(part) is not None
            and all(p.exists() for p in transcript_paths(part).values()))


# --- driver --------------------------------------------------------------

def prefetch(parts: list[Part], workers: int,
             failures: Optional[list[str]] = None) -> Iterator[Part]:
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
                # Record it, don't just print it: a download that never happened is
                # a missing part, and a summary reading "0 failed" beside three
                # absent videos is worse than no summary at all.
                gone = "gone from YouTube" in str(outcome)
                print(f"  [{'gone' if gone else 'fail'}] {part.name} download: "
                      f"{str(outcome)[-160:]}")
                if failures is not None:
                    failures.append(part.name)
                continue
            yield part
    finally:
        done.set()


def run_season(season: int, *, limit: Optional[int], download_only: bool,
               workers: int, refresh: bool, fake: bool, whisper: bool) -> dict[str, int]:
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

    # Link everything already on disk, not just what this run fetches — otherwise
    # parts downloaded before the naming existed stay unnamed forever.
    for p in parts:
        link_video(p)

    pending = [p for p in parts if not is_done(p)]
    if len(pending) < len(parts):
        print(f"  {len(parts) - len(pending)} already done, {len(pending)} to go")
    if not pending:
        return {"done": len(parts), "failed": 0}

    transcriber: Optional[transcribe.Transcriber] = None
    if fake:
        transcriber = transcribe.FakeTranscriber()
    elif whisper:
        transcriber = transcribe.WhisperTranscriber()

    stats = {"done": len(parts) - len(pending), "failed": 0}
    gone: list[str] = []
    started = time.monotonic()
    dl_failed: list[str] = []
    for i, part in enumerate(prefetch(pending, workers, dl_failed), 1):
        link_video(part)
        if download_only:
            print(f"  [{i}/{len(pending)}] {part.name} downloaded")
            stats["done"] += 1
            continue
        try:
            if transcriber is not None:
                sentences = transcribe_part(part, transcriber)
            else:
                try:
                    sentences = transcribe_from_captions(part)
                except media.MediaError as exc:
                    # Not every upload has auto-captions. Falling back to Whisper
                    # costs ~45s for that one part and leaves the series complete,
                    # where failing would leave a hole you'd only find later.
                    #
                    # But only when the track is really absent: a throttled fetch
                    # raises here too, and transcribing that locally would spend the
                    # 45s to work around a wait, then bake the worse transcript in.
                    if "throttled" in str(exc):
                        raise
                    print(f"  [whisper] {part.name}: no caption track", flush=True)
                    sentences = transcribe_part(part, whisper_fallback())
            write_transcripts(part, sentences)
        except Exception as exc:                          # noqa: BLE001
            if "gone from YouTube" in str(exc):
                # Not a failure to retry later — the video does not exist any more.
                gone.append(part.name)
                print(f"  [gone] {part.name}: taken down at source")
            else:
                print(f"  [fail] {part.name}: {type(exc).__name__}: {exc}")
            stats["failed"] += 1
            continue
        stats["done"] += 1
        rate = (time.monotonic() - started) / i
        left = int(rate * (len(pending) - i))
        print(f"  [{i}/{len(pending)}] {part.name}  {len(sentences)} sentences"
              f"  (~{left // 60}m{left % 60:02d}s left)", flush=True)
    stats["failed"] += len(dl_failed)
    if gone or dl_failed:
        names = sorted(set(gone) | set(dl_failed))
        # State it plainly: re-running will not fill the blocked ones, so they are
        # a gap in the season rather than something to try again tonight.
        print(f"  {len(names)} part(s) missing: {', '.join(names)}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, nargs="+", default=[1], choices=sorted(PLAYLISTS))
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N parts of each season (smoke test)")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the videos, skip transcription")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel downloads — 1 by default because concurrency is "
                         "what draws YouTube's throttling; raise it at your peril")
    ap.add_argument("--refresh", action="store_true",
                    help="re-list the playlists instead of using the saved manifest")
    ap.add_argument("--cookies-from-browser", default=None, metavar="BROWSER",
                    help="lift a YouTube session from this browser (e.g. 'chrome', "
                         "'firefox:/path/to/profile'). A bulk run trips YouTube's "
                         "bot detection and then everything needs sign-in; this is "
                         "what gets past it. Authenticates every request as you.")
    ap.add_argument("--whisper", action="store_true",
                    help="transcribe with Whisper instead of YouTube's auto-captions: "
                         "~20x slower, but punctuated and generally more accurate")
    ap.add_argument("--fake", action="store_true",
                    help="canned transcripts, for exercising the plumbing offline")
    args = ap.parse_args()
    if args.cookies_from_browser:
        # Set on config rather than threaded through every call site, so the shared
        # downloader in services/media picks it up too and there stays one place
        # that knows how to talk to yt-dlp.
        config.YTDLP_COOKIES_FROM_BROWSER = args.cookies_from_browser

    totals = {"done": 0, "failed": 0}
    for season in args.season:
        s = run_season(season, limit=args.limit, download_only=args.download_only,
                       workers=args.workers, refresh=args.refresh, fake=args.fake,
                       whisper=args.whisper)
        for k in totals:
            totals[k] += s[k]
        print(f"  season {season}: {s['done']} done, {s['failed']} failed")

    print(f"\ntotal: {totals['done']} done, {totals['failed']} failed")
    print(f"transcripts under {Path(config.MEDIA_CACHE_DIR) / 'series'}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
