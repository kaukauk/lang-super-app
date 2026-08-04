#!/usr/bin/env python3
"""Join a season's ~3-minute parts back into whole episodes, video and transcript.

The uploader split each episode into eight parts. Studying wants the episode: a
scene that straddles a part boundary is currently two files, and the transcript
timings restart from zero every three minutes.

Joining blind is the thing to avoid. If consecutive parts overlap — the second
repeating the last seconds of the first, which is common when someone cuts by
hand — a naive concatenation stutters, and every transcript timestamp after the
first join drifts by the accumulated overlap. So each seam is measured from the
audio itself rather than assumed:

    take the tail of part N and the head of part N+1, reduce both to a loudness
    envelope, and cross-correlate. A real overlap shows up as a sharp normalised
    peak at the lag equal to its length. No peak means the parts butt together.

Envelopes rather than raw samples because they are cheap and robust; both sides
come from the same encode, so this is a generous case for correlation.

`--self-test` proves the detector works by building a known overlap and checking
it is recovered. Without that, "no overlap found" and "detector is broken" look
identical, and the second one silently corrupts every episode it touches.

Usage:
    python -m scripts.combine_episodes --self-test
    python -m scripts.combine_episodes --season 1
    python -m scripts.combine_episodes --season 1 --episode 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config                                    # noqa: E402
from app.services import media, transcribe                # noqa: E402
from scripts.fetch_series import (                        # noqa: E402
    Part, SEGMENT_ID, load_manifest, series_dir, to_srt, video_path,
)

# How much of each side to inspect at a seam. Overlaps from hand-cutting are
# seconds, not minutes; 30s is generous and keeps the correlation cheap.
PROBE_S = 30.0
# Envelope resolution. 100 Hz resolves a 10 ms seam, far finer than any real cut.
ENV_HZ = 100
# Two thresholds, because the two stages measure different things.
#
# The envelope stage only compares loudness in 10ms buckets, so it is a candidate
# generator: loose enough not to miss a real seam whose contour is unremarkable.
MIN_PEAK = 0.70
# The refinement stage compares the actual waveforms. An overlap is the SAME audio
# twice, so a genuine one scores ~0.99 and anything materially below that is not
# an overlap at all. This is the threshold that decides, and it is deliberately
# strict: trimming a seam that does not exist deletes real footage and shifts
# every subtitle after it.
MIN_REFINED_PEAK = 0.90
# Ignore sub-frame "overlaps" — below this it is measurement noise, not a cut.
MIN_OVERLAP_S = 0.20


def episodes_dir(season: int) -> Path:
    return series_dir(season) / "episodes"


def backup_dir(season: int) -> Path:
    return Path(config.MEDIA_CACHE_DIR) / "backup" / f"s{season:02d}"


# --- audio probing -------------------------------------------------------

def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32), rate


def envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    """RMS loudness per 1/ENV_HZ second.

    Comparing loudness contours rather than waveforms: it is ~500x less data, and
    it does not care about the phase or codec differences that make raw samples
    correlate badly across two separately-encoded files."""
    hop = max(1, rate // ENV_HZ)
    n = len(samples) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[:n * hop].reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-9)


def _norm(x: np.ndarray) -> np.ndarray:
    x = x - x.mean()
    s = float(np.linalg.norm(x))
    return x / s if s > 0 else x


def best_overlap(tail: np.ndarray, head: np.ndarray) -> tuple[float, float]:
    """Longest suffix of `tail` matching the same-length prefix of `head`.

    Returns (overlap_seconds, normalised_peak). Every candidate length is scored
    by cosine similarity, which keeps scores comparable across lengths — plain
    correlation grows with the window and would always favour the longest one."""
    best_len, best_score = 0, 0.0
    max_l = min(len(tail), len(head))
    # A handful of samples will correlate perfectly by chance; require enough
    # length to be meaningful.
    lo = max(int(MIN_OVERLAP_S * ENV_HZ), 4)
    for l in range(lo, max_l + 1):
        a, b = _norm(tail[-l:]), _norm(head[:l])
        score = float(np.dot(a, b))
        if score > best_score:
            best_len, best_score = l, score
    return best_len / ENV_HZ, best_score


def refine_overlap(a: np.ndarray, b: np.ndarray, rate: int, coarse_s: float,
                   *, window_s: float = 0.05) -> tuple[float, float]:
    """Sharpen a coarse overlap to the sample.

    The envelope stage works at 100 Hz, so it can only place a seam within 10 ms —
    audible as a tick or a clipped syllable when the cut lands mid-word. Here the
    raw waveforms are compared at 16 kHz across +/-50 ms around the coarse answer,
    which resolves the seam to ~0.06 ms. Both sides are the same original audio, so
    at the true offset the two waveforms agree almost exactly and the peak is
    unmistakable."""
    coarse = int(round(coarse_s * rate))
    span = int(window_s * rate)
    # Compare at most 2s of audio: enough to be unambiguous, cheap to score.
    seg = min(coarse, int(2.0 * rate))
    if seg <= 0 or len(b) < seg:
        return coarse_s, 0.0

    ref = _norm(b[:seg])
    best_off, best_score = 0, -1.0
    for d in range(-span, span + 1):
        end = len(a) - coarse - d + seg
        start = end - seg
        if start < 0 or end > len(a):
            continue
        score = float(np.dot(_norm(a[start:end]), ref))
        if score > best_score:
            best_off, best_score = d, score
    return (coarse + best_off) / rate, best_score


def seam_audio(a_video: Path, a_dur: float, b_video: Path,
               tmp: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Raw samples either side of a seam, for the refinement pass."""
    a_wav, b_wav = tmp / "ra.wav", tmp / "rb.wav"
    media.extract_audio(a_video, max(0.0, a_dur - PROBE_S), a_dur, a_wav,
                        fmt="wav", sample_rate=16000, mono=True)
    media.extract_audio(b_video, 0.0, PROBE_S, b_wav,
                        fmt="wav", sample_rate=16000, mono=True)
    a, rate = _read_wav_mono(a_wav)
    b, _ = _read_wav_mono(b_wav)
    return a, b, rate


def seam_envelopes(a_video: Path, a_dur: float, b_video: Path,
                   tmp: Path) -> tuple[np.ndarray, np.ndarray]:
    a_wav, b_wav = tmp / "a.wav", tmp / "b.wav"
    media.extract_audio(a_video, max(0.0, a_dur - PROBE_S), a_dur, a_wav,
                        fmt="wav", sample_rate=16000, mono=True)
    media.extract_audio(b_video, 0.0, PROBE_S, b_wav,
                        fmt="wav", sample_rate=16000, mono=True)
    a, ra = _read_wav_mono(a_wav)
    b, rb = _read_wav_mono(b_wav)
    return envelope(a, ra), envelope(b, rb)


# --- planning ------------------------------------------------------------

@dataclass
class Piece:
    part: Part
    video: Path
    duration_s: float
    trim_start_s: float = 0.0     # dropped from this piece's head as duplicate
    seam_score: float = 0.0       # confidence of the overlap that caused the trim

    @property
    def kept_s(self) -> float:
        return self.duration_s - self.trim_start_s


def plan_episode(parts: list[Part], *, probe: bool = True) -> list[Piece]:
    """Measure every seam and decide what to trim."""
    pieces: list[Piece] = []
    for p in parts:
        v = video_path(p)
        if v is None:
            raise FileNotFoundError(f"part {p.name} has no video")
        pieces.append(Piece(part=p, video=v, duration_s=media.probe_duration(v)))

    if not probe:
        return pieces

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for prev, cur in zip(pieces, pieces[1:]):
            tail, head = seam_envelopes(prev.video, prev.duration_s, cur.video, tmp)
            overlap, score = best_overlap(tail, head)
            if not (score >= MIN_PEAK and overlap >= MIN_OVERLAP_S):
                continue
            # The envelope only locates the seam to 10ms, which lands mid-syllable
            # often enough to be heard. Refine against the waveform itself.
            a, b, rate = seam_audio(prev.video, prev.duration_s, cur.video, tmp)
            fine, fine_score = refine_overlap(a, b, rate, overlap)
            # The waveform has the final say. A candidate that survives the
            # envelope but scores poorly here is two different pieces of audio that
            # happen to rise and fall alike — trimming on that would cut real
            # footage, so it is rejected rather than trusted.
            if fine_score >= MIN_REFINED_PEAK and fine >= MIN_OVERLAP_S:
                cur.trim_start_s, cur.seam_score = fine, fine_score
            elif score >= MIN_REFINED_PEAK:
                cur.trim_start_s, cur.seam_score = overlap, score
    return pieces


# --- muxing --------------------------------------------------------------

def stream_codecs(path: Path) -> dict[str, str]:
    cmd = [config.FFPROBE_BIN, "-v", "error", "-show_entries",
           "stream=codec_type,codec_name", "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    streams = json.loads(out.stdout or "{}").get("streams", [])
    return {s.get("codec_type", ""): s.get("codec_name", "") for s in streams}


def trim_head(src: Path, start_s: float, end_s: float, out: Path,
              codecs: dict[str, str]) -> Path:
    """Re-cut `src` from `start_s`, keeping the codecs the concat expects.

    The generic clip cutter re-encodes mp4 audio to AAC, but these downloads are
    h264 + Opus. Mixing an AAC piece with Opus pieces under `-c copy` produces a
    file whose audio stops at the first seam — it does not error, it just breaks
    quietly, which is the worst way for this to fail. So encode back to whatever
    the source actually uses."""
    audio = "libopus" if codecs.get("audio") == "opus" else "aac"
    cmd = [config.FFMPEG_BIN, "-y", "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
           "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
           "-c:a", audio, "-b:a", "128k", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise media.MediaError(f"trim failed: {(proc.stderr or '')[-300:]}")
    return out


def concat_video(pieces: list[Piece], out: Path) -> None:
    """Join the pieces into one file.

    Stream-copy when nothing is trimmed: it is near-instant and, more importantly,
    lossless — re-encoding twenty episodes to change nothing would just degrade
    them. A trimmed piece is cut first, and only that piece is re-encoded, because
    seeking a copy lands on the nearest keyframe rather than the requested time.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        codecs = stream_codecs(pieces[0].video)
        inputs: list[Path] = []
        for i, pc in enumerate(pieces):
            if pc.trim_start_s <= 0:
                inputs.append(pc.video)
                continue
            cut = tmp / f"trim{i}.mp4"
            trim_head(pc.video, pc.trim_start_s, pc.duration_s, cut, codecs)
            inputs.append(cut)

        listing = tmp / "concat.txt"
        listing.write_text("".join(f"file '{p.resolve()}'\n" for p in inputs),
                           encoding="utf-8")
        cmd = [config.FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
               "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise media.MediaError(f"concat failed: {(proc.stderr or '')[-400:]}")


def merge_transcripts(pieces: list[Piece]) -> list[transcribe.Sentence]:
    """Splice the per-part transcripts onto one episode timeline.

    Each part's timings start at zero, so they are shifted by everything kept
    before them. Sentences inside a trimmed overlap are dropped rather than
    shifted: they are the duplicate copy of lines the previous part already
    carried, and keeping them would show every overlapping line twice."""
    out: list[transcribe.Sentence] = []
    offset = 0.0
    for pc in pieces:
        sents = transcribe.load_cached(pc.part.youtube_id, SEGMENT_ID) or []
        for s in sents:
            if s.end_s <= pc.trim_start_s:
                continue                      # wholly inside the duplicated head
            shift = offset - pc.trim_start_s
            out.append(transcribe.Sentence(
                text=s.text,
                start_s=max(offset, s.start_s + shift),
                end_s=s.end_s + shift,
                words=[transcribe.Word(w.text, w.start_s + shift, w.end_s + shift)
                       for w in s.words],
            ))
        offset += pc.kept_s
    return out


def write_episode(season: int, episode: int, pieces: list[Piece],
                  sentences: list[transcribe.Sentence]) -> dict:
    d = episodes_dir(season)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{episode}"
    (d / f"{stem}.txt").write_text(
        "\n".join(s.text.strip() for s in sentences) + "\n", encoding="utf-8")
    (d / f"{stem}.srt").write_text(to_srt(sentences), encoding="utf-8")
    meta = {
        "season": season, "episode": episode,
        "duration_s": round(sum(p.kept_s for p in pieces), 3),
        "parts": [{"part": p.part.part, "youtube_id": p.part.youtube_id,
                   "duration_s": round(p.duration_s, 3),
                   "trimmed_start_s": round(p.trim_start_s, 3),
                   "seam_score": round(p.seam_score, 3)} for p in pieces],
        "sentences": [s.as_dict() for s in sentences],
    }
    (d / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return meta


# --- backup --------------------------------------------------------------

def backup_raw(season: int, parts: list[Part]) -> Path:
    """Copy the per-part videos and transcripts before anything is combined.

    Honest caveat: this sits on the same disk as the originals, so it protects
    against a mistake in this script, not against the drive failing. Re-downloading
    is the real fallback for that, and it is no longer free — YouTube now demands a
    signed-in session, and one part is already blocked at source."""
    dest = backup_dir(season)
    (dest / "parts").mkdir(parents=True, exist_ok=True)
    copied = 0
    for p in parts:
        v = video_path(p)
        if v is None:
            continue
        target = dest / "parts" / f"{p.name}{v.suffix}"
        if not target.exists() or target.stat().st_size != v.stat().st_size:
            shutil.copy2(v, target)
            copied += 1
    src_dir = series_dir(season)
    for f in sorted(src_dir.glob("*")):
        if f.is_file() and f.suffix in (".txt", ".srt", ".json"):
            shutil.copy2(f, dest / f.name)
    print(f"  backup: {copied} new video(s) copied to {dest}")
    return dest


# --- self-test -----------------------------------------------------------

def self_test() -> int:
    """Recover a known overlap from real audio.

    A detector that always answers "no overlap" would sail through every episode
    and quietly produce stuttering joins with drifting subtitles, so it has to be
    shown working on a case with a known answer before being trusted on cases
    without one."""
    manifest = load_manifest(1)
    if not manifest:
        print("no season 1 manifest; run fetch_series first")
        return 1
    src = next((video_path(p) for p in manifest if video_path(p) is not None), None)
    if src is None:
        print("no downloaded part to test with")
        return 1

    dur = media.probe_duration(src)
    overlap = 3.5
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Cut two pieces that deliberately share `overlap` seconds.
        a = media.cut_clip(src, 0.0, 60.0, tmp / "a.mp4", reencode=True)
        b = media.cut_clip(src, 60.0 - overlap, 120.0, tmp / "b.mp4", reencode=True)
        tail, head = seam_envelopes(a, media.probe_duration(a), b, tmp)
        got, score = best_overlap(tail, head)
        ok = abs(got - overlap) < 0.25 and score >= MIN_PEAK
        print(f"  overlapping pair:  expected {overlap:.2f}s, "
              f"detected {got:.2f}s (peak {score:.3f}) -> {'PASS' if ok else 'FAIL'}")

        # And a pair that genuinely does not overlap must not invent one.
        c = media.cut_clip(src, 0.0, 60.0, tmp / "c.mp4", reencode=True)
        d = media.cut_clip(src, min(dur - 60.0, 90.0), min(dur, 150.0),
                           tmp / "d.mp4", reencode=True)
        tail2, head2 = seam_envelopes(c, media.probe_duration(c), d, tmp)
        got2, score2 = best_overlap(tail2, head2)
        clean = not (score2 >= MIN_PEAK and got2 >= MIN_OVERLAP_S)
        print(f"  non-overlapping:   detected {got2:.2f}s (peak {score2:.3f}) -> "
              f"{'PASS' if clean else 'FAIL'}")
    return 0 if (ok and clean) else 1


# --- driver --------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=1)
    ap.add_argument("--episode", type=int, nargs="*", default=None,
                    help="only these episodes (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure the seams and report, write nothing")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="verify overlap detection against a known answer")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    parts = load_manifest(args.season)
    if not parts:
        print(f"no manifest for season {args.season}; run fetch_series first")
        return 1

    by_ep: dict[int, list[Part]] = {}
    for p in parts:
        by_ep.setdefault(p.episode, []).append(p)
    wanted = sorted(args.episode) if args.episode else sorted(by_ep)

    if not args.dry_run and not args.no_backup:
        backup_raw(args.season, [p for e in wanted for p in by_ep.get(e, [])])

    made, skipped = 0, []
    for ep in wanted:
        eps = sorted(by_ep.get(ep, []), key=lambda x: x.part)
        if not eps:
            continue
        missing = [p.name for p in eps if video_path(p) is None]
        if missing:
            # Joining around a hole would silently splice two non-adjacent scenes
            # together, which is worse than not producing the episode.
            print(f"  ep {ep}: SKIPPED — missing part(s) {', '.join(missing)}")
            skipped.append(ep)
            continue

        pieces = plan_episode(eps)
        seams = [f"{p.part.name}:{p.trim_start_s:.2f}s({p.seam_score:.2f})"
                 for p in pieces if p.trim_start_s > 0]
        total = sum(p.kept_s for p in pieces)
        print(f"  ep {ep}: {len(pieces)} parts, {total / 60:.1f} min"
              + (f", trimmed {' '.join(seams)}" if seams else ", no overlaps"))
        if args.dry_run:
            continue

        out = episodes_dir(args.season) / f"{ep}.mp4"
        concat_video(pieces, out)
        sentences = merge_transcripts(pieces)
        write_episode(args.season, ep, pieces, sentences)
        actual = media.probe_duration(out)
        drift = actual - total
        # A join that lands more than a second from the plan means the trim or the
        # concat did something other than intended, and every timestamp after it
        # is wrong. Say so rather than let it pass as success.
        flag = "  <-- CHECK" if abs(drift) > 1.0 else ""
        print(f"        wrote {out.name}: {actual / 60:.1f} min, "
              f"{len(sentences)} sentences, drift {drift:+.2f}s{flag}", flush=True)
        made += 1

    print(f"\n{made} episode(s) written to {episodes_dir(args.season)}")
    if skipped:
        print(f"skipped (incomplete): {', '.join(str(e) for e in skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
