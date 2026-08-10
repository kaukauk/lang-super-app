"""FastAPI app: a thin HTTP layer over the service functions (same logic the CLI
uses). Serves the built React SPA from frontend/dist when present.

Run:  uvicorn app.main:app --host 127.0.0.1 --port 8765
Set LANGAPP_FAKE_LLM=1 to use the offline DemoLLM (used by the API tests)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .anki.connect import AnkiConnectClient
from .llm import OpenAIClient
from .llm.fake import DemoLLM
from .services import (anki_io, avcards, chunkcards, extraction, ingest, library,
                       sparring, srs, transcribe, tts, vocab)

app = FastAPI(title="Korean Immersion App")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _fake() -> bool:
    return bool(os.environ.get("LANGAPP_FAKE_LLM"))


def provider():
    return DemoLLM() if _fake() else OpenAIClient()


def transcriber():
    return transcribe.FakeTranscriber() if _fake() else transcribe.WhisperTranscriber()


def tts_provider():
    return tts.FakeTTS() if _fake() else tts.EdgeTTS()


def _model(override: Optional[str], default: str) -> str:
    return override or default


# --- schemas -------------------------------------------------------------

class IngestBody(BaseModel):
    url: Optional[str] = None
    path: Optional[str] = None
    languages: list[str] = ["ko"]
    title: Optional[str] = None
    replace: bool = False


class IngestSeasonBody(BaseModel):
    season: int
    replace: bool = False


class CardBody(BaseModel):
    ko: str
    en: str = ""
    gloss: str = ""
    kind: str = "manual"


class ExtractBody(BaseModel):
    accept: bool = False
    model: Optional[str] = None
    today: Optional[int] = None


class SparStartBody(BaseModel):
    segment_id: Optional[int] = None


class SparTurnBody(BaseModel):
    text: str
    deck: str = "Korean"
    scene: str = ""
    targets: Optional[list[str]] = None
    model: Optional[str] = None
    wind_down: bool = False


class HarvestBody(BaseModel):
    deck: str = "Korean"
    accept: bool = False
    model: Optional[str] = None
    today: Optional[int] = None
    # push the harvest to its own Anki deck as Korean-only production cards
    push_anki: bool = False


class ImportBody(BaseModel):
    content: str
    deck: str = "Korean"
    schedule_now: bool = False


# --- routes --------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "db": config.DB_PATH}


@app.get("/api/videos")
def videos() -> list[dict[str, Any]]:
    return [{"id": v.id, "youtube_id": v.youtube_id, "title": v.title,
             "duration_s": v.duration_s,
             "added_at": v.created_at.isoformat() if v.created_at else None}
            for v in ingest.list_videos()]


@app.get("/api/videos/{video_id}")
def video_detail(video_id: int) -> dict[str, Any]:
    """One video, enough to play it straight through outside the day plan."""
    v = ingest.get_video(video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    return {"id": v.id, "youtube_id": v.youtube_id, "title": v.title,
            "duration_s": v.duration_s, "source": v.source,
            "playable": bool(v.source == "local" and v.local_path
                             and Path(v.local_path).exists()),
            "series": v.series, "season": v.season, "episode": v.episode,
            "part_label": v.part_label}


@app.get("/api/videos/{video_id}/cues")
def video_cues(video_id: int) -> list[dict[str, Any]]:
    """Every subtitle cue for the whole video, for watching it end to end."""
    if ingest.get_video(video_id) is None:
        raise HTTPException(404, "video not found")
    cues = ingest.get_cues_in_range(video_id, 0.0, float("inf"))
    return [{"start_s": c.start_s, "dur_s": c.dur_s, "text": c.text} for c in cues]


@app.get("/api/library")
def library_overview() -> dict[str, Any]:
    """Everything the library screen renders, in one request."""
    return library.overview()


@app.post("/api/library/ingest-season")
def library_ingest_season(body: IngestSeasonBody) -> dict[str, Any]:
    try:
        return library.ingest_season(body.season, replace=body.replace)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/segments/{segment_id}/done")
def segment_done(segment_id: int, done: bool = True) -> dict[str, Any]:
    try:
        return (library.mark_segment_done(segment_id) if done
                else library.unmark_segment(segment_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/videos/{video_id}/stream")
def video_stream(video_id: int):
    """Serve a joined episode straight from disk.

    Whole file rather than a per-day clip: the player seeks to the day's range
    itself, so a day can be watched the moment it is ingested instead of waiting
    for a build step. FileResponse handles Range requests, which is what makes
    seeking work at all."""
    video = ingest.get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    if video.source != "local" or not video.local_path:
        raise HTTPException(404, "not a local video")
    path = Path(video.local_path)
    if not path.exists():
        raise HTTPException(404, f"file missing: {path}")
    return FileResponse(str(path), media_type="video/mp4")


@app.post("/api/videos/ingest")
def ingest_video(body: IngestBody) -> dict[str, Any]:
    try:
        if body.path:
            return ingest.ingest_from_file(body.path, title=body.title, replace=body.replace)
        if body.url:
            return ingest.ingest_from_youtube(body.url, languages=body.languages,
                                              title=body.title, replace=body.replace)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(400, "provide url or path")


@app.get("/api/videos/{video_id}/segments")
def segments(video_id: int) -> list[dict[str, Any]]:
    done = library.done_segment_ids()
    return [{"id": s.id, "day": s.day_number, "start_s": s.start_s, "end_s": s.end_s,
             "is_full_review": s.is_full_review, "done": s.id in done}
            for s in ingest.get_segments(video_id)]


@app.get("/api/segments/{segment_id}")
def segment_detail(segment_id: int) -> dict[str, Any]:
    seg = ingest.get_segment(segment_id)
    if seg is None:
        raise HTTPException(404, "segment not found")
    video = ingest.get_video(seg.video_id)
    from .services import media
    has_clip = bool(video) and (media.segment_dir(video.youtube_id, segment_id) / "snippet.mp4").exists()
    return {"id": seg.id, "video_id": seg.video_id,
            "youtube_id": video.youtube_id if video else None,
            "title": video.title if video else "",
            "day_number": seg.day_number, "start_s": seg.start_s, "end_s": seg.end_s,
            "is_full_review": seg.is_full_review, "has_clip": has_clip,
            # A local episode is one file on disk, so the player can seek to the
            # day's range directly — no per-day clip build needed before watching.
            "source": video.source if video else "youtube",
            "part_label": video.part_label if video else None}


@app.get("/api/segments/{segment_id}/cues")
def segment_cues(segment_id: int) -> list[dict[str, Any]]:
    seg = ingest.get_segment(segment_id)
    if seg is None:
        raise HTTPException(404, "segment not found")
    cues = ingest.get_cues_in_range(seg.video_id, seg.start_s, seg.end_s)
    return [{"start_s": c.start_s, "dur_s": c.dur_s, "text": c.text} for c in cues]


class BuildAvBody(BaseModel):
    push: bool = True
    make_media: bool = True
    replace: bool = False
    model: Optional[str] = None
    url: Optional[str] = None


@app.post("/api/segments/{segment_id}/build-av-cards")
def build_av(segment_id: int, body: BuildAvBody) -> dict[str, Any]:
    try:
        anki = AnkiConnectClient() if body.push else None
        res = avcards.build_av_cards(
            segment_id, llm=provider(), transcriber=transcriber(),
            tts_provider=tts_provider(), anki=anki,
            make_media=body.make_media, push=body.push, replace=body.replace,
            model=body.model, url=body.url)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return res.as_dict()


@app.get("/api/segments/{segment_id}/primer")
def primer(segment_id: int) -> list[dict[str, Any]]:
    return avcards.primer_for_segment(segment_id)


@app.get("/api/segments/{segment_id}/primer-audio/{idx}")
def primer_audio(segment_id: int, idx: int):
    rows = avcards.primer_for_segment(segment_id)
    match = next((r for r in rows if r["target_idx"] == idx), None)
    if match is None or not match.get("audio_path") or not Path(match["audio_path"]).exists():
        raise HTTPException(404, "primer audio not found")
    return FileResponse(match["audio_path"], media_type="audio/mpeg")


class SeenBody(BaseModel):
    seen: Optional[bool] = None


@app.post("/api/segments/{segment_id}/primer/{idx}/seen")
def primer_seen(segment_id: int, idx: int, body: SeenBody) -> dict[str, Any]:
    """Between-passes check: did the learner catch this line on the blind watch?"""
    try:
        return avcards.mark_seen(segment_id, idx, body.seen)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/segments/{segment_id}/primer-clip/{idx}")
def primer_clip(segment_id: int, idx: int):
    """The target's scene clip — the same file the Anki card embeds."""
    path = avcards.clip_path_for(segment_id, idx)
    if path is None:
        raise HTTPException(404, "clip not built for this target")
    # VP9/Opus for Anki's sake; browsers play it too, so one file serves both
    suffix = path.suffix.lstrip(".") or "mp4"
    return FileResponse(str(path), media_type=f"video/{'webm' if suffix == 'webm' else 'mp4'}")


@app.get("/api/segments/{segment_id}/clip")
def segment_clip(segment_id: int):
    seg = ingest.get_segment(segment_id)
    if seg is None:
        raise HTTPException(404, "segment not found")
    video = ingest.get_video(seg.video_id)
    from .services import media
    snippet = media.segment_dir(video.youtube_id, segment_id) / "snippet.mp4" if video else None
    if snippet is None or not snippet.exists():
        raise HTTPException(404, "snippet not built yet (run build-av-cards)")
    return FileResponse(str(snippet), media_type="video/mp4")


@app.get("/api/anki/check")
def anki_check() -> dict[str, Any]:
    try:
        client = AnkiConnectClient()
        return {"ok": True, "url": client.url, "version": client.version(),
                "decks": client.invoke("deckNames")}
    except Exception as exc:
        raise HTTPException(503, str(exc))


class AnnotateBody(BaseModel):
    texts: list[str]
    deck: str = "Korean"
    # Off skips the LLM entirely: colours and 아요/어요 forms still come back, only
    # the glosses for words the deck has never seen are missing.
    translate: bool = True
    # Re-read Anki instead of trusting the cached index. The player sets this on the
    # first request of a session, so starting a video always reflects reviews done
    # since the last one.
    refresh: bool = False


@app.post("/api/annotate")
def annotate(body: AnnotateBody) -> dict[str, Any]:
    """Word-level annotation of subtitle lines — what to highlight, and how."""
    llm = None
    if body.translate:
        # No key configured is not a reason to fail the request: the colours and the
        # deck's own glosses are the useful half and neither needs a model.
        try:
            llm = provider()
        except Exception:
            llm = None
    lines = vocab.annotate(body.texts, provider=llm, deck=body.deck,
                           translate=body.translate, refresh=body.refresh)
    return {"lines": vocab.as_dicts(lines)}


class VocabAddBody(BaseModel):
    word: str
    context_ko: str
    gloss_en: str = ""
    pos: str = ""
    display: str = ""
    # Where the word sits in the line. The same word can occur twice, so the
    # position is what identifies which clause the card is being judged against.
    start: Optional[int] = None


@app.post("/api/vocab/add")
def vocab_add(body: VocabAddBody) -> dict[str, Any]:
    """Add one clicked subtitle word to Anki."""
    try:
        return vocab.add_to_anki(word=body.word, context_ko=body.context_ko,
                                 gloss_en=body.gloss_en, pos=body.pos,
                                 display=body.display, start=body.start,
                                 tts_provider=tts_provider())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/segments/{segment_id}/extract")
def extract(segment_id: int, body: ExtractBody) -> dict[str, Any]:
    try:
        result = extraction.extract_segment(
            segment_id, provider(), model=_model(body.model, config.EXTRACTION_MODEL))
    except Exception as exc:
        raise HTTPException(400, str(exc))
    out: dict[str, Any] = {"primer_md": result.primer_md, "counts": result.counts(),
                           "warnings": result.warnings,
                           "chunks": [c.as_dict() for c in result.chunks]}
    if body.accept:
        out["accepted"] = len(extraction.accept_chunks(result.chunks, segment_id=segment_id))
    return out


class AcceptBody(BaseModel):
    chunks: list[dict]
    segment_id: Optional[int] = None
    today: Optional[int] = None


@app.post("/api/cards/accept")
def accept_chunks_ep(body: AcceptBody) -> dict[str, Any]:
    """Accept (human-reviewed) extracted chunks as new cards due tomorrow."""
    from .services.extraction import Chunk
    chunks = [
        Chunk(type=c.get("type", "sentence"), ko=c["ko"],
              en=c.get("en", ""), gloss=c.get("gloss", ""))
        for c in body.chunks if c.get("ko")
    ]
    return {"accepted": len(extraction.accept_chunks(chunks, segment_id=body.segment_id))}


@app.get("/api/chunks/browse")
def chunks_browse() -> dict[str, Any]:
    """Harvested chunk cards, live from Anki. Anki owns reviewing; this is read-only."""
    try:
        return chunkcards.browse()
    except Exception as exc:
        raise HTTPException(503, f"Anki is not reachable: {exc}")


@app.get("/api/chunks/audio/{filename}")
def chunk_audio(filename: str):
    """Serve a chunk's TTS from our own cache rather than Anki's collection.media —
    same file, and it means browsing works without reaching into Anki's data dir."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "bad filename")
    path = Path(config.MEDIA_CACHE_DIR) / "chunks" / filename
    if not path.exists():
        raise HTTPException(404, "audio not found")
    return FileResponse(str(path), media_type="audio/mpeg")


@app.get("/api/stats")
def stats(deck: str = "Korean", today: Optional[int] = None) -> dict[str, Any]:
    """Card count, read from Anki so the site can never disagree with it."""
    try:
        client = AnkiConnectClient()
        return {"total": len(client.invoke("findNotes", query=f'deck:"{deck}"') or []),
                "chunks": len(client.invoke(
                    "findNotes", query=f'deck:"{config.ANKI_CHUNK_DECK}"') or []),
                "source": "anki"}
    except Exception as exc:
        return {"total": None, "chunks": None, "source": "unavailable", "error": str(exc)}


@app.get("/api/cards")
def cards(deck: str = "Korean") -> list[dict[str, Any]]:
    return [{"id": c.id, "kind": c.kind, "front_ko": c.front_ko, "back_en": c.back_en,
             "gloss": c.gloss, "state": c.state} for c in srs.list_cards(deck_name=deck)]


@app.post("/api/cards")
def add_card(body: CardBody) -> dict[str, Any]:
    c = srs.add_card(front_ko=body.ko, back_en=body.en, gloss=body.gloss,
                     kind=body.kind, surface_forms=[body.ko], state="new")
    return {"id": c.id, "front_ko": c.front_ko}


@app.get("/api/export.txt", response_class=PlainTextResponse)
def export(deck: str = "Korean") -> str:
    return anki_io.export_txt_string(deck_name=deck)


@app.post("/api/import")
def import_(body: ImportBody) -> dict[str, Any]:
    return anki_io.import_txt_string(body.content, deck_name=body.deck,
                                     schedule_now=body.schedule_now)


@app.post("/api/spar/start")
def spar_start(body: SparStartBody) -> dict[str, Any]:
    sess = sparring.start_session(segment_id=body.segment_id)
    return {"session_id": sess.id, "segment_id": sess.segment_id}


@app.post("/api/spar/{session_id}/open")
def spar_open(session_id: int, body: SparStartBody) -> dict[str, Any]:
    opening = sparring.spar_open(session_id, provider=provider(),
                                 segment_id=body.segment_id)
    return {"anchor_ko": opening.anchor_ko, "role_en": opening.role_en,
            "prompt_en": opening.prompt_en, "scene_en": opening.scene_en,
            "glosses": opening.glosses}


@app.post("/api/spar/{session_id}/turn")
def spar_turn(session_id: int, body: SparTurnBody) -> dict[str, Any]:
    targets = body.targets
    if targets is None:
        targets = [c.front_ko for c in srs.list_cards(deck_name=body.deck)
                   if c.state == "new"][:15]
    # Words the character is allowed to speak in, so seen-only: a card imported but
    # never studied is not vocabulary the learner can follow, and listing it here is
    # what makes sparring talk over their head. This is also the only list the
    # 400-item truncation below ever threatened — the studied set is far smaller.
    support = sorted(srs.known_surface_forms(deck_name=body.deck, seen_only=True)
                     - set(targets))
    sess = sparring.get_session(session_id)
    role = sess.role_en if sess else ""
    scene = body.scene or (sess.scene_en if sess else "")
    history = sparring.session_history(session_id)
    sparring.record_turn(session_id, "user", text_ko=body.text)
    resp = sparring.spar_turn(user_text=body.text, provider=provider(), targets=targets,
                              support=support, scene=scene, history=history, role=role,
                              wind_down=body.wind_down,
                              model=_model(body.model, config.SPARRING_MODEL))
    sparring.record_turn(session_id, "assistant", text_ko=resp.reply_ko,
                         glosses=resp.glosses, targets_used=resp.targets_used,
                         feedback={"micro_feedback_en": resp.micro_feedback_en,
                                   "corrections": [c.as_dict() for c in resp.corrections]})
    return {"reply_ko": resp.reply_ko, "micro_feedback_en": resp.micro_feedback_en,
            "corrections": [c.as_dict() for c in resp.corrections],
            "suggested_user_reply_ko": resp.suggested_user_reply_ko,
            "suggested_user_reply_en": resp.suggested_user_reply_en,
            "targets_used": resp.targets_used, "unexpected": resp.unexpected,
            "warnings": resp.warnings, "glosses": resp.glosses}


class LookupBody(BaseModel):
    word: str
    context_ko: str = ""
    # glosses the turn already returned, so a content word costs no LLM call
    known_glosses: dict[str, str] = {}


@app.post("/api/spar/{session_id}/lookup")
def spar_lookup(session_id: int, body: LookupBody) -> dict[str, Any]:
    """Click-to-gloss mid-sparring. Records the lookup so the harvest can offer it."""
    try:
        out = sparring.lookup_word(body.word, body.context_ko, provider=provider(),
                                   known_glosses=body.known_glosses)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    sparring.record_lookup(session_id, word=body.word, context_ko=body.context_ko,
                           gloss_en=out["gloss_en"], ending=out.get("ending", ""))
    return out


@app.post("/api/spar/{session_id}/harvest")
def spar_harvest(session_id: int, body: HarvestBody) -> dict[str, Any]:
    history = sparring.session_history(session_id)
    # Drops harvested chunks the learner already knows. Seen-only for the same
    # reason: a chunk is worth keeping if they cannot yet produce it, and an
    # unstudied card is no evidence that they can.
    known = srs.known_surface_forms(deck_name=body.deck, seen_only=True)
    corrections = sparring.session_corrections(session_id)
    lookups = sparring.session_lookups(session_id)
    chunks = sparring.harvest_chunks(history=history, provider=provider(), known=known,
                                     corrections=corrections)
    out: dict[str, Any] = {"chunks": [c.as_dict() for c in chunks],
                           "corrections": corrections, "lookups": lookups}
    if body.accept:
        out["accepted"] = len(extraction.accept_chunks(chunks, deck_name=body.deck))
    if body.push_anki:
        # Anki is optional and often simply not running; a failed push must not
        # discard a harvest the learner just earned.
        try:
            out["anki"] = chunkcards.push_chunks(
                chunks, corrections=corrections, history=history, lookups=lookups,
                session_id=session_id, tts_provider=tts_provider())
        except Exception as exc:
            out["anki"] = {"error": str(exc), "pushed": 0}
    return out


# --- static frontend (mounted last so /api/* wins) ----------------------

_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
