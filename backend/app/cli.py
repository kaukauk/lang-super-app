"""Command-line interface to the whole app, so every piece of functionality is
driveable and testable from the terminal (only the visual UI is left for manual
testing).

Run:  python -m app.cli <command> ...     (after activating backend/.venv)
LLM commands take --fake to use the offline DemoLLM (no API spend).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import config, db
from .anki.connect import AnkiConnectClient
from .llm import FakeLLM, OpenAIClient  # noqa: F401
from .llm.fake import DemoLLM
from .services import anki_io, avcards, extraction, ingest, media, sparring, srs, transcribe, tts


def _print(obj: Any) -> None:
    if isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def _provider(args):
    return DemoLLM() if getattr(args, "fake", False) else OpenAIClient()


def _targets_and_support(deck: str, limit: int = 15) -> tuple[list[str], list[str]]:
    """Today's targets = the freshly-added (still 'new') cards; support = the rest
    of the known deck vocabulary."""
    targets = [c.front_ko for c in srs.list_cards(deck_name=deck) if c.state == "new"][:limit]
    support = sorted(srs.known_surface_forms(deck_name=deck) - set(targets))
    return targets, support


# --- command handlers ----------------------------------------------------

def cmd_init_db(args):
    db.init_db()
    _print("db initialised at " + db.config.DB_PATH)


def cmd_videos(args):
    rows = ingest.list_videos()
    _print([{"id": v.id, "youtube_id": v.youtube_id, "title": v.title,
             "duration_min": round(v.duration_s / 60, 1)} for v in rows])


def cmd_ingest_file(args):
    _print(ingest.ingest_from_file(args.path, youtube_id=args.id, title=args.title,
                                   replace=args.replace))


def cmd_ingest_url(args):
    _print(ingest.ingest_from_youtube(args.url, languages=args.lang, title=args.title,
                                      replace=args.replace))


def cmd_segments(args):
    segs = ingest.get_segments(args.video_id)
    _print([{"id": s.id, "day": s.day_number, "start_s": round(s.start_s, 1),
             "end_s": round(s.end_s, 1), "len_min": round((s.end_s - s.start_s) / 60, 2),
             "full_review": s.is_full_review} for s in segs])


def cmd_segment_text(args):
    _print(ingest.segment_text(args.segment_id))


def cmd_extract(args):
    result = extraction.extract_segment(args.segment_id, _provider(args), model=args.model)
    out = {
        "primer_md": result.primer_md,
        "counts": result.counts(),
        "warnings": result.warnings,
        "chunks": [c.as_dict() for c in result.chunks],
    }
    if args.accept:
        cards = extraction.accept_chunks(result.chunks, segment_id=args.segment_id,
                                         today_ord=args.today)
        out["accepted_card_ids"] = [c.id for c in cards]
    _print(out)


def cmd_stats(args):
    _print(srs.stats(deck_name=args.deck, today_ord=args.today))


def cmd_due(args):
    cards = srs.due_cards(deck_name=args.deck, today_ord=args.today, limit=args.limit)
    _print([{"id": c.id, "kind": c.kind, "front_ko": c.front_ko, "back_en": c.back_en,
             "gloss": c.gloss, "state": c.state, "interval": c.interval} for c in cards])


def cmd_grade(args):
    c = srs.review_card(args.card_id, args.grade, today_ord=args.today)
    _print({"id": c.id, "state": c.state, "interval": c.interval, "ease": c.ease,
            "reps": c.reps, "lapses": c.lapses, "due_ord": c.due_ord})


def cmd_add_card(args):
    c = srs.add_card(front_ko=args.ko, back_en=args.en, gloss=args.gloss, kind=args.kind,
                     surface_forms=[args.ko], due_ord=args.today, state="new")
    _print({"id": c.id, "front_ko": c.front_ko})


def cmd_cards(args):
    _print([{"id": c.id, "kind": c.kind, "front_ko": c.front_ko, "back_en": c.back_en,
             "state": c.state, "due_ord": c.due_ord} for c in srs.list_cards(deck_name=args.deck)])


def cmd_export_txt(args):
    n = anki_io.export_txt(args.path, deck_name=args.deck)
    _print(f"exported {n} cards to {args.path}")


def cmd_import_txt(args):
    _print(anki_io.import_txt(args.path, deck_name=args.deck, schedule_now=args.schedule_now))


def cmd_spar_start(args):
    sess = sparring.start_session(segment_id=args.segment)
    _print({"session_id": sess.id, "segment_id": sess.segment_id})


def cmd_spar(args):
    targets, support = _targets_and_support(args.deck)
    if args.targets:
        targets = args.targets
    sess = sparring.get_session(args.session)
    role = sess.role_en if sess else ""
    scene = args.scene or (sess.scene_en if sess else "")
    history = sparring.session_history(args.session)
    sparring.record_turn(args.session, "user", text_ko=args.text)
    resp = sparring.spar_turn(
        user_text=args.text, provider=_provider(args), targets=targets,
        support=support, scene=scene, history=history, model=args.model,
        role=role, wind_down=args.wind_down,
    )
    sparring.record_turn(args.session, "assistant", text_ko=resp.reply_ko,
                         glosses=resp.glosses, targets_used=resp.targets_used)
    _print({
        "reply_ko": resp.reply_ko,
        "micro_feedback_en": resp.micro_feedback_en,
        "suggested_user_reply_ko": resp.suggested_user_reply_ko,
        "suggested_user_reply_en": resp.suggested_user_reply_en,
        "targets_used": resp.targets_used,
        "unexpected": resp.unexpected,
        "warnings": resp.warnings,
    })


def cmd_harvest(args):
    history = sparring.session_history(args.session)
    known = srs.known_surface_forms(deck_name=args.deck)
    chunks = sparring.harvest_chunks(history=history, provider=_provider(args), known=known)
    out: dict[str, Any] = {"chunks": [c.as_dict() for c in chunks]}
    if args.accept:
        cards = extraction.accept_chunks(chunks, today_ord=args.today, deck_name=args.deck)
        out["accepted_card_ids"] = [c.id for c in cards]
    _print(out)


# --- media / AV cards / anki ---------------------------------------------

def cmd_media_fetch(args):
    vp = media.ensure_video(args.id, url=args.url, section=args.section)
    _print({"youtube_id": vp.youtube_id, "video": str(vp.video),
            "duration_s": round(vp.duration_s, 1)})


def cmd_anki_check(args):
    client = AnkiConnectClient()
    out = {"url": client.url, "version": client.version(),
           "decks": client.invoke("deckNames"),
           "models": client.invoke("modelNames")}
    _print(out)


def cmd_build_av(args):
    llm = _provider(args)
    transcriber = transcribe.FakeTranscriber() if args.fake else transcribe.WhisperTranscriber()
    tts_provider = tts.FakeTTS() if args.fake else tts.EdgeTTS()
    # --fake is fully offline: no ffmpeg/yt-dlp, no Anki.
    make_media = not (args.no_media or args.fake)
    push = not (args.no_push or args.fake)
    anki = None if not push else AnkiConnectClient()
    res = avcards.build_av_cards(
        args.segment_id, llm=llm, transcriber=transcriber, tts_provider=tts_provider,
        anki=anki, make_media=make_media, push=push, replace=args.replace,
        model=args.model, url=args.url, section=args.section)
    _print(res.as_dict())


def cmd_primer(args):
    _print(avcards.primer_for_segment(args.segment_id))


def cmd_spar_open(args):
    opening = sparring.spar_open(args.session, provider=_provider(args),
                                 segment_id=args.segment, model=args.model)
    _print({"anchor_ko": opening.anchor_ko, "role_en": opening.role_en,
            "prompt_en": opening.prompt_en, "scene_en": opening.scene_en,
            "glosses": opening.glosses})


# --- argument parser -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.cli", description="Korean immersion app CLI")
    sub = p.add_subparsers(dest="command", required=True)

    def add_llm_flags(sp):
        sp.add_argument("--fake", action="store_true", help="use offline DemoLLM (no API)")
        sp.add_argument("--model", default=None, help="override LLM model")

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("videos").set_defaults(func=cmd_videos)

    sp = sub.add_parser("ingest-file"); sp.add_argument("path")
    sp.add_argument("--id"); sp.add_argument("--title"); sp.add_argument("--replace", action="store_true")
    sp.set_defaults(func=cmd_ingest_file)

    sp = sub.add_parser("ingest-url"); sp.add_argument("url")
    sp.add_argument("--lang", nargs="+", default=["ko"]); sp.add_argument("--title")
    sp.add_argument("--replace", action="store_true"); sp.set_defaults(func=cmd_ingest_url)

    sp = sub.add_parser("segments"); sp.add_argument("video_id", type=int); sp.set_defaults(func=cmd_segments)
    sp = sub.add_parser("segment-text"); sp.add_argument("segment_id", type=int); sp.set_defaults(func=cmd_segment_text)

    sp = sub.add_parser("extract"); sp.add_argument("segment_id", type=int)
    sp.add_argument("--accept", action="store_true"); sp.add_argument("--today", type=int, default=None)
    add_llm_flags(sp); sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("stats"); sp.add_argument("--deck", default="Korean")
    sp.add_argument("--today", type=int, default=None); sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("due"); sp.add_argument("--deck", default="Korean")
    sp.add_argument("--today", type=int, default=None); sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_due)

    sp = sub.add_parser("grade"); sp.add_argument("card_id", type=int)
    sp.add_argument("grade", choices=srs.GRADES); sp.add_argument("--today", type=int, default=None)
    sp.set_defaults(func=cmd_grade)

    sp = sub.add_parser("add-card"); sp.add_argument("--ko", required=True)
    sp.add_argument("--en", default=""); sp.add_argument("--gloss", default="")
    sp.add_argument("--kind", default="manual"); sp.add_argument("--today", type=int, default=None)
    sp.set_defaults(func=cmd_add_card)

    sp = sub.add_parser("cards"); sp.add_argument("--deck", default="Korean"); sp.set_defaults(func=cmd_cards)

    sp = sub.add_parser("export-txt"); sp.add_argument("path"); sp.add_argument("--deck", default="Korean")
    sp.set_defaults(func=cmd_export_txt)
    sp = sub.add_parser("import-txt"); sp.add_argument("path"); sp.add_argument("--deck", default="Korean")
    sp.add_argument("--schedule-now", action="store_true"); sp.set_defaults(func=cmd_import_txt)

    sp = sub.add_parser("spar-start"); sp.add_argument("--segment", type=int, default=None)
    sp.set_defaults(func=cmd_spar_start)

    sp = sub.add_parser("spar-open"); sp.add_argument("session", type=int)
    sp.add_argument("--segment", type=int, default=None); add_llm_flags(sp)
    sp.set_defaults(func=cmd_spar_open)

    sp = sub.add_parser("spar"); sp.add_argument("session", type=int); sp.add_argument("text")
    sp.add_argument("--deck", default="Korean"); sp.add_argument("--scene", default="")
    sp.add_argument("--targets", nargs="*", default=None)
    sp.add_argument("--wind-down", action="store_true", dest="wind_down")
    add_llm_flags(sp); sp.set_defaults(func=cmd_spar)

    sp = sub.add_parser("harvest"); sp.add_argument("session", type=int)
    sp.add_argument("--deck", default="Korean"); sp.add_argument("--accept", action="store_true")
    sp.add_argument("--today", type=int, default=None); add_llm_flags(sp); sp.set_defaults(func=cmd_harvest)

    # --- media / AV cards / anki ---
    sp = sub.add_parser("media-fetch"); sp.add_argument("id"); sp.add_argument("--url", default=None)
    sp.add_argument("--section", default=None); sp.set_defaults(func=cmd_media_fetch)

    sp = sub.add_parser("anki-check"); sp.set_defaults(func=cmd_anki_check)

    sp = sub.add_parser("build-av-cards"); sp.add_argument("segment_id", type=int)
    sp.add_argument("--no-push", action="store_true", dest="no_push")
    sp.add_argument("--no-media", action="store_true", dest="no_media")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument("--url", default=None)
    sp.add_argument("--section", default=None, help='yt-dlp download window, e.g. "*0:00-30:00"')
    add_llm_flags(sp); sp.set_defaults(func=cmd_build_av)

    sp = sub.add_parser("primer"); sp.add_argument("segment_id", type=int)
    sp.set_defaults(func=cmd_primer)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db.init_db()
    try:
        args.func(args)
    except Exception as exc:  # surface a clean message
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
