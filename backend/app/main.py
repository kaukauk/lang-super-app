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
from .services import (anki_io, avcards, chunkcards, extraction, ingest,
                       sparring, srs, transcribe, tts)

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


class GradeBody(BaseModel):
    grade: str


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
             "duration_s": v.duration_s} for v in ingest.list_videos()]


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
    return [{"id": s.id, "day": s.day_number, "start_s": s.start_s, "end_s": s.end_s,
             "is_full_review": s.is_full_review} for s in ingest.get_segments(video_id)]


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
            "is_full_review": seg.is_full_review, "has_clip": has_clip}


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
        cards = extraction.accept_chunks(result.chunks, segment_id=segment_id, today_ord=body.today)
        out["accepted_card_ids"] = [c.id for c in cards]
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
    cards = extraction.accept_chunks(chunks, segment_id=body.segment_id, today_ord=body.today)
    return {"accepted": len(cards)}


@app.get("/api/reviews/due")
def due(deck: str = "Korean", today: Optional[int] = None, limit: Optional[int] = None):
    return [{"id": c.id, "kind": c.kind, "front_ko": c.front_ko, "back_en": c.back_en,
             "gloss": c.gloss, "state": c.state} for c in
            srs.due_cards(deck_name=deck, today_ord=today, limit=limit)]


@app.post("/api/reviews/{card_id}/grade")
def grade(card_id: int, body: GradeBody) -> dict[str, Any]:
    try:
        c = srs.review_card(card_id, body.grade)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"id": c.id, "state": c.state, "interval": c.interval, "ease": c.ease,
            "due_ord": c.due_ord}


@app.get("/api/stats")
def stats(deck: str = "Korean", today: Optional[int] = None) -> dict[str, int]:
    return srs.stats(deck_name=deck, today_ord=today)


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
    support = sorted(srs.known_surface_forms(deck_name=body.deck) - set(targets))
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
    known = srs.known_surface_forms(deck_name=body.deck)
    corrections = sparring.session_corrections(session_id)
    lookups = sparring.session_lookups(session_id)
    chunks = sparring.harvest_chunks(history=history, provider=provider(), known=known,
                                     corrections=corrections)
    out: dict[str, Any] = {"chunks": [c.as_dict() for c in chunks],
                           "corrections": corrections, "lookups": lookups}
    if body.accept:
        cards = extraction.accept_chunks(chunks, today_ord=body.today, deck_name=body.deck)
        out["accepted_card_ids"] = [c.id for c in cards]
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
