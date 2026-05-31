"""Extraction tests — offline via FakeLLM. Uses real Korean so the lemma-based
dedupe behaves realistically."""

from __future__ import annotations

import pytest

from app.llm import FakeLLM
from app.llm.client import LLMError
from app.services import extraction as ex
from app.services import ingest, srs

# distinct content lemmas across types
DEF_SENT = ["가다", "오다", "사다", "팔다", "앉다"]
DEF_NV = ["밥을 먹다", "물을 마시다", "책을 읽다", "공을 차다", "노래를 부르다"]
DEF_GRAM = ["쓰고 싶다", "듣고 있다", "웃을 수 있다", "울기 전에", "걷는 중"]


def chunk(t, ko):
    return {"type": t, "ko": ko, "en": f"en-{ko}", "gloss": "g"}


def response(sent=None, nv=None, gram=None, primer="Scene 1. Stuff happens."):
    sent = DEF_SENT if sent is None else sent
    nv = DEF_NV if nv is None else nv
    gram = DEF_GRAM if gram is None else gram
    chunks = (
        [chunk("sentence", k) for k in sent]
        + [chunk("noun_verb", k) for k in nv]
        + [chunk("grammar", k) for k in gram]
    )
    return {"primer_md": primer, "chunks": chunks}


def test_parse_valid_and_invalid() -> None:
    r = ex.parse_result(response())
    assert r.counts() == {"sentence": 5, "noun_verb": 5, "grammar": 5}
    with pytest.raises(LLMError):
        ex.parse_result({"chunks": []})
    with pytest.raises(LLMError):
        ex.parse_result({"primer_md": "x", "chunks": "nope"})
    with pytest.raises(LLMError):
        ex.parse_result({"primer_md": "x", "chunks": [{"type": "bad", "ko": "가다"}]})


def test_happy_path_single_call() -> None:
    llm = FakeLLM(responses=[response()])
    r = ex.extract_text("대본", llm)
    assert r.counts() == {"sentence": 5, "noun_verb": 5, "grammar": 5}
    assert len(llm.calls) == 1


def test_rewrite_on_bad_counts() -> None:
    llm = FakeLLM(responses=[response(sent=["가다"]), response()])
    r = ex.extract_text("대본", llm)
    assert r.counts()["sentence"] == 5
    assert len(llm.calls) == 2
    assert "EXACTLY" in llm.calls[1]["user"]


def test_rewrite_on_invalid_json() -> None:
    llm = FakeLLM(responses=[{"primer_md": "", "chunks": []}, response()])
    r = ex.extract_text("대본", llm)
    assert len(r.chunks) == 15
    assert len(llm.calls) == 2


def test_dedupe_against_known_then_topup() -> None:
    known = {"가다", "오다"}                         # 2 of the sentences are known
    topup = response(sent=["서다", "눕다"], nv=[], gram=[])
    llm = FakeLLM(responses=[response(), topup])
    r = ex.extract_text("대본", llm, known_forms=known)
    assert r.counts() == {"sentence": 5, "noun_verb": 5, "grammar": 5}
    sentence_kos = {c.ko for c in r.chunks if c.type == "sentence"}
    assert "가다" not in sentence_kos and "오다" not in sentence_kos
    assert {"서다", "눕다"} <= sentence_kos
    assert len(llm.calls) == 2


def test_primer_hangul_warning() -> None:
    llm = FakeLLM(responses=[response(primer="장면 1. 한국어가 있어요.")])
    r = ex.extract_text("대본", llm)
    assert any("Korean" in w for w in r.warnings)


def test_extract_segment_and_accept_creates_due_cards() -> None:
    cues = [{"text": f"{i}번 줄이에요", "start": i * 2.0, "duration": 2.0} for i in range(400)]
    summary = ingest.ingest_cues("vidEXTRACT1", cues, title="t")
    seg = [s for s in ingest.get_segments(summary["video_id"]) if not s.is_full_review][0]

    llm = FakeLLM(responses=[response()])
    result = ex.extract_segment(seg.id, llm)
    assert len(result.chunks) == 15

    cards = ex.accept_chunks(result.chunks, segment_id=seg.id, today_ord=5000)
    assert len(cards) == 15
    assert all(c.due_ord == 5001 for c in cards)
    assert len(srs.due_cards(today_ord=5000)) == 0
    assert len(srs.due_cards(today_ord=5001)) == 15
