"""Build the day's AV cards from the video.

Pipeline (see the plan): download once → cut the ~5-min snippet → Whisper the
segment audio for accurate sentence timing → LLM picks 5 targets → per target,
make the FRONT original-audio clip + the BACK context->target video clip + the
in-app PRIMER edge-tts audio → push notes+media to Anki via AnkiConnect → record
`AvCard` rows.

Dependencies (LLM / transcriber / TTS / Anki) are injected so the whole thing
runs offline under `--fake` with no model, network, or Anki."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from sqlmodel import delete, select

from .. import config, db
from ..anki.connect import AnkiConnectClient, NoteSpec
from ..llm.client import LLMProvider
from ..models import AvCard, Segment, Video
from . import extraction, ingest, media, srs, transcribe, tts
from .transcribe import Sentence, Transcriber, Word


@dataclass
class BuiltTarget:
    target_idx: int
    target_ko: str
    context_ko: str
    explanation_en: str
    start_s: float            # absolute (within the whole video)
    end_s: float
    context_start_s: float
    audio_file: str = ""      # front original audio (collection.media name)
    clip_file: str = ""       # back video clip (collection.media name)
    primer_audio: str = ""    # absolute path to in-app primer mp3 (NOT in Anki)
    anki_note_id: Optional[int] = None

    def media_name(self, kind: str, ext: str, youtube_id: str, segment_id: int) -> str:
        # Include a hash of the cut span so a rebuild that changes the cut produces a
        # NEW filename — Anki caches media by name and would otherwise keep showing a
        # stale clip from an earlier build.
        sig = f"{self.start_s:.2f}-{self.end_s:.2f}-{self.context_start_s:.2f}"
        h = hashlib.sha1(sig.encode()).hexdigest()[:8]
        return f"ki_{youtube_id}_{segment_id}_{self.target_idx}_{h}_{kind}.{ext}"


@dataclass
class AvBuildResult:
    segment_id: int
    youtube_id: str
    targets: list[BuiltTarget] = field(default_factory=list)
    snippet_path: str = ""
    pushed: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "segment_id": self.segment_id, "youtube_id": self.youtube_id,
            "snippet_path": self.snippet_path, "pushed": self.pushed,
            "warnings": self.warnings,
            "targets": [{
                "target_idx": t.target_idx, "target_ko": t.target_ko,
                "context_ko": t.context_ko, "explanation_en": t.explanation_en,
                "start_s": round(t.start_s, 3), "end_s": round(t.end_s, 3),
                "context_start_s": round(t.context_start_s, 3),
                "audio_file": t.audio_file, "clip_file": t.clip_file,
                "primer_audio": t.primer_audio, "anki_note_id": t.anki_note_id,
            } for t in self.targets],
        }


def _hangul(s: str) -> str:
    return re.sub(r"[^가-힣]", "", s)


def relocate_target(
    transcriber: Transcriber, src: Path, seg_dir: Path, text: str,
    approx_start: float, approx_end: float, *, window_s: float, min_ratio: float = 0.6,
) -> Optional[tuple[float, float]]:
    """Re-transcribe a window around Whisper's estimate and return the TRUE absolute
    [start, end] of `text` (corrects turbo's timestamp drift). Works at WORD level so
    it returns a tight span for the target, not a merged sentence. None if not found."""
    ws = max(0.0, approx_start - window_s)
    we = approx_end + window_s
    probe = seg_dir / "_probe.wav"
    try:
        media.extract_audio(src, ws, we, probe, fmt="wav", sample_rate=16000, mono=True)
        sents = transcriber.transcribe(probe)
    except Exception:
        return None
    finally:
        if probe.exists():
            try:
                probe.unlink()
            except OSError:
                pass

    target = _hangul(text)
    if not target:
        return None

    # Flatten words; build a hangul string with each word's char range + time span.
    concat = ""
    spans: list[tuple[int, int, float, float]] = []
    for s in sents:
        for w in (s.words or [Word(s.text, s.start_s, s.end_s)]):
            h = _hangul(w.text)
            if not h:
                continue
            spans.append((len(concat), len(concat) + len(h), w.start_s, w.end_s))
            concat += h

    # exact substring of the target's words -> tight, accurate span. The phrase may
    # occur more than once in the window (common lines) — pick the occurrence whose
    # time is closest to Whisper's estimate.
    def _span_at(pos: int) -> Optional[tuple[float, float]]:
        end = pos + len(target)
        cov = [sp for sp in spans if sp[1] > pos and sp[0] < end]
        return (ws + min(c[2] for c in cov), ws + max(c[3] for c in cov)) if cov else None

    occurrences, p = [], concat.find(target)
    while p != -1:
        sp = _span_at(p)
        if sp:
            occurrences.append(sp)
        p = concat.find(target, p + 1)
    if occurrences:
        return min(occurrences, key=lambda sp: abs(sp[0] - approx_start))

    # fallback: best whole-sentence fuzzy match (handles minor transcription diffs)
    best, best_score = None, 0.0
    for s in sents:
        cand = _hangul(s.text)
        if cand and (sc := SequenceMatcher(None, target, cand).ratio()) > best_score:
            best, best_score = s, sc
    if best is not None and best_score >= min_ratio:
        return (ws + best.start_s, ws + best.end_s)
    return None


def pick_context(sentences: list[Sentence], idx: int, *, max_back: int = 4) -> Optional[Sentence]:
    """The lead-up context: the nearest preceding FULL sentence (skip fragments like
    interjections), searching back up to `max_back` sentences. Falls back to the
    immediately preceding sentence if none qualifies."""
    if idx <= 0:
        return None
    for j in range(idx - 1, max(-1, idx - 1 - max_back), -1):
        if extraction.is_full_sentence(sentences[j].text):
            return sentences[j]
    return sentences[idx - 1]


def clip_has_text(transcriber: Transcriber, path: Path, text: str, *, min_ratio: float = 0.5) -> bool:
    """True if the cut media actually contains `text` (guards against misaligned
    cuts). Lenient — on a transcription error we don't drop the card."""
    try:
        heard = "".join(_hangul(s.text) for s in transcriber.transcribe(path))
    except Exception:
        return True
    t = _hangul(text)
    if not t:
        return True
    if t in heard or (len(t) > 5 and t[2:] in heard):
        return True
    return SequenceMatcher(None, t, heard).ratio() >= min_ratio


def _existing_cards(segment_id: int) -> list[AvCard]:
    with db.session_scope() as s:
        return list(s.exec(
            select(AvCard).where(AvCard.segment_id == segment_id)
            .order_by(AvCard.target_idx)).all())


def build_av_cards(
    segment_id: int, *, llm: LLMProvider, transcriber: Transcriber,
    tts_provider: Optional[tts.TTS] = None, anki: Optional[AnkiConnectClient] = None,
    make_media: bool = True, push: bool = True, replace: bool = False,
    model: Optional[str] = None, url: Optional[str] = None,
    section: Optional[str] = None, n: int = config.AV_TARGETS_PER_SEGMENT,
) -> AvBuildResult:
    seg: Optional[Segment] = ingest.get_segment(segment_id)
    if seg is None:
        raise ValueError(f"no segment {segment_id}")
    video: Optional[Video] = ingest.get_video(seg.video_id)
    if video is None:
        raise ValueError(f"segment {segment_id} has no video")
    youtube_id = video.youtube_id

    old_cards = _existing_cards(segment_id)
    if old_cards and not replace:
        raise ValueError(
            f"segment {segment_id} already has AV cards (pass replace=True to rebuild)")

    result = AvBuildResult(segment_id=segment_id, youtube_id=youtube_id)
    seg_dir = media.segment_dir(youtube_id, segment_id)
    seg_dir.mkdir(parents=True, exist_ok=True)

    # --- transcribe the segment audio (accurate per-sentence timing) ---
    sentences: Optional[list[Sentence]] = transcribe.load_cached(youtube_id, segment_id)
    src_path: Optional[Path] = None
    if make_media:
        vp = media.ensure_video(youtube_id, url=url, section=section)
        src_path = vp.video
        snippet = seg_dir / "snippet.mp4"
        if not snippet.exists():
            media.cut_clip(src_path, seg.start_s, seg.end_s, snippet, reencode=False)
        result.snippet_path = str(snippet)
        if sentences is None:
            wav = seg_dir / "segment.wav"
            media.extract_audio(src_path, seg.start_s, seg.end_s, wav,
                                fmt="wav", sample_rate=16000, mono=True)
            sentences = transcriber.transcribe(wav)
            transcribe.save_cache(youtube_id, segment_id, sentences)
    elif sentences is None:
        # offline/fake path: no ffmpeg — let the (fake) transcriber produce sentences.
        sentences = transcriber.transcribe(seg_dir / "segment.wav")

    if not sentences:
        result.warnings.append("transcription produced no sentences")
        return result

    texts = [s.text for s in sentences]
    known = srs.known_surface_forms(deck_name=config.ANKI_DECK)
    # over-pick so we can drop misaligned cards and still reach n good ones
    candidates = extraction.pick_targets(texts, llm, known=known, n=n + 5, model=model)
    if not candidates:
        result.warnings.append("LLM selected no targets")
        return result

    tts_provider = tts_provider or tts.EdgeTTS()
    base = seg.start_s  # Whisper times are within-segment; add base for absolute cuts
    lead, tail = config.AV_PAD_LEAD_S, config.AV_PAD_TAIL_S
    vid_dur = vp.duration_s if (make_media and src_path is not None) else None

    def pad(start: float, end: float) -> tuple[float, float]:
        """Breathing room so clips don't cut off the last syllable; end stays in bounds."""
        s = max(0.0, start - lead)
        e = end + tail
        if vid_dur is not None:
            e = min(e, vid_dur)
        return s, e

    # Anki setup + replace-cleanup happens ONCE up front.
    if make_media and push and src_path is not None:
        anki = anki or AnkiConnectClient()
        anki.ensure_deck(config.ANKI_DECK)
        anki.ensure_model(config.ANKI_MODEL)
        if replace:
            anki.delete_notes_by_tag(f"seg{segment_id}")
            for oc in old_cards:
                for fn in (oc.audio_file, oc.clip_file):
                    if fn:
                        try:
                            anki.invoke("deleteMediaFile", filename=fn)
                            (seg_dir / fn).unlink(missing_ok=True)
                        except Exception:
                            pass

    accepted = 0
    for p in candidates:
        if accepted >= n:
            break
        sent = sentences[p.index]
        ctx = pick_context(sentences, p.index, max_back=config.AV_CONTEXT_MAX_LOOKBACK)
        bt = BuiltTarget(
            target_idx=accepted, target_ko=sent.text, context_ko=ctx.text if ctx else "",
            explanation_en=p.explanation_en,
            start_s=base + sent.start_s, end_s=base + sent.end_s,
            context_start_s=base + (ctx.start_s if ctx else sent.start_s),
        )

        if make_media and src_path is not None:
            # snap to where the target text actually is
            if config.AV_VERIFY_ALIGN:
                loc = relocate_target(transcriber, src_path, seg_dir, sent.text,
                                      bt.start_s, bt.end_s, window_s=config.AV_ALIGN_WINDOW_S)
                delta = 0.0
                if loc is not None:
                    delta = loc[0] - bt.start_s
                    bt.start_s, bt.end_s = loc
                # relocate the context sentence independently so the clip's lead-up
                # starts cleanly on it; fall back to the target's local drift.
                if ctx is not None:
                    cloc = relocate_target(transcriber, src_path, seg_dir, ctx.text,
                                           base + ctx.start_s, base + ctx.end_s,
                                           window_s=config.AV_ALIGN_WINDOW_S)
                    cstart = cloc[0] if cloc is not None else bt.context_start_s + delta
                    # context must sit before the target and within the lead cap
                    cstart = min(cstart, bt.start_s)
                    cstart = max(cstart, bt.start_s - config.AV_CONTEXT_MAX_LEAD_S)
                    bt.context_start_s = max(0.0, cstart)
            # name AFTER realignment so the cache-busting hash reflects the final cut
            audio_name = bt.media_name("front", "mp3", youtube_id, segment_id)
            a_start, a_end = pad(bt.start_s, bt.end_s)
            audio_path = media.extract_audio(src_path, a_start, a_end, seg_dir / audio_name)

            # verify-and-drop: if the cut doesn't contain the target, skip this card
            if config.AV_VERIFY_CLIP and not clip_has_text(transcriber, audio_path, sent.text):
                (seg_dir / audio_name).unlink(missing_ok=True)
                result.warnings.append(f"dropped misaligned target: {sent.text!r}")
                continue

            clip_name = bt.media_name("clip", config.AV_CLIP_EXT, youtube_id, segment_id)
            c_start, c_end = pad(bt.context_start_s, bt.end_s)
            clip_path = media.cut_clip(src_path, c_start, c_end, seg_dir / clip_name, reencode=True)
            bt.audio_file, bt.clip_file = audio_name, clip_name

            if push:
                anki.store_media_file(audio_name, audio_path)
                anki.store_media_file(clip_name, clip_path)
                note = NoteSpec(
                    target_ko=bt.target_ko, target_audio_file=audio_name,
                    context_ko=bt.context_ko, clip_file=clip_name,
                    explanation_en=bt.explanation_en,
                    tags=["korean-immersion", f"seg{segment_id}"],
                )
                bt.anki_note_id = anki.add_note(note, deck=config.ANKI_DECK,
                                                model=config.ANKI_MODEL)
                if bt.anki_note_id:
                    result.pushed += 1

        # in-app primer audio (clean TTS) — only for accepted cards; never to Anki
        bt.primer_audio = str(tts.synth_cached(
            sent.text, seg_dir, provider=tts_provider, prefix=f"primer_{segment_id}_{accepted}"))
        result.targets.append(bt)
        accepted += 1

    if accepted < n:
        result.warnings.append(f"only {accepted}/{n} targets verified (rest dropped as misaligned)")
    _persist(segment_id, result, replace=replace)
    return result


def _persist(segment_id: int, result: AvBuildResult, *, replace: bool) -> None:
    with db.session_scope() as s:
        if replace:
            s.exec(delete(AvCard).where(AvCard.segment_id == segment_id))
        for t in result.targets:
            s.add(AvCard(
                segment_id=segment_id, target_idx=t.target_idx, target_ko=t.target_ko,
                context_ko=t.context_ko, explanation_en=t.explanation_en,
                start_s=t.start_s, end_s=t.end_s, context_start_s=t.context_start_s,
                audio_file=t.audio_file, clip_file=t.clip_file,
                primer_audio_file=t.primer_audio, anki_note_id=t.anki_note_id,
            ))


def primer_for_segment(segment_id: int) -> list[dict]:
    """The in-app Hit-List: target Korean + clean TTS audio + in-context hint.
    Reads the AvCard rows produced by build_av_cards.

    `seen` and `has_clip` drive the between-passes check. The clip PATH stays out of
    the payload — it is served through its own endpoint, so a primer response can be
    rendered without exposing local filesystem layout."""
    cards = _existing_cards(segment_id)
    return [{
        "target_idx": c.target_idx, "target_ko": c.target_ko,
        "hint_en": c.explanation_en,
        "audio_path": c.primer_audio_file,
        "context_ko": c.context_ko,
        "seen": c.seen,
        "has_clip": bool(c.clip_file),
    } for c in cards]


def mark_seen(segment_id: int, target_idx: int, seen: Optional[bool]) -> dict:
    """Record whether the learner caught this line on the blind watch. `seen=None`
    clears it, so a mis-tap can be undone without rebuilding the segment."""
    with db.session_scope() as s:
        card = s.exec(
            select(AvCard).where(AvCard.segment_id == segment_id,
                                 AvCard.target_idx == target_idx)).first()
        if card is None:
            raise ValueError(f"no target {target_idx} in segment {segment_id}")
        card.seen = seen
        s.add(card)
        return {"target_idx": target_idx, "seen": seen}


def clip_path_for(segment_id: int, target_idx: int) -> Optional[Path]:
    """Absolute path of a target's video clip, or None if it was never built.

    Same file the Anki card embeds — the check page shows the scene exactly as the
    card will, so recognising it here is the same act as recognising it in review."""
    card = next((c for c in _existing_cards(segment_id) if c.target_idx == target_idx), None)
    if card is None or not card.clip_file:
        return None
    seg: Optional[Segment] = ingest.get_segment(segment_id)
    video: Optional[Video] = ingest.get_video(seg.video_id) if seg else None
    if video is None:
        return None
    path = media.segment_dir(video.youtube_id, segment_id) / card.clip_file
    return path if path.exists() else None
