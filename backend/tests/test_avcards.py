"""AV card building (offline): target selection, context = previous sentence,
clip/audio spans, AvCard persistence, and the primer surface."""

from __future__ import annotations

import pytest

from app.llm.fake import DemoLLM
from app.services import avcards, extraction, ingest, media, tts
from app.services.transcribe import FakeTranscriber, Sentence, Word


@pytest.fixture
def cache_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(media.config, "MEDIA_CACHE_DIR", str(tmp_path))
    return tmp_path


def _make_segment() -> int:
    """Create a video + one ~5-min segment so build_av_cards has something to run."""
    cues = [{"text": f"문장{i}", "start": float(i * 5), "duration": 5.0} for i in range(60)]
    summary = ingest.ingest_cues("vidAVCARDS01", cues, title="t", replace=True)
    segs = [s for s in ingest.get_segments(summary["video_id"]) if not s.is_full_review]
    return segs[0].id


def _sentences():
    raw = [("오늘 어디 가요?", 0.0, 1.6), ("저는 시장에 가요.", 1.8, 3.4),
           ("자전거를 타고 싶어요.", 3.6, 5.2), ("날씨가 좋아요.", 5.4, 7.0),
           ("우리 같이 가요.", 7.2, 8.6), ("네, 좋아요.", 8.8, 9.8)]
    return [Sentence(t, a, b, [Word(t, a, b)]) for (t, a, b) in raw]


def test_pick_targets_aligns_to_indices() -> None:
    sents = [s.text for s in _sentences()]
    picks = extraction.pick_targets(sents, DemoLLM(), n=5)
    assert len(picks) == 5
    for p in picks:
        assert sents[p.index] == p.ko
        assert p.explanation_en


def test_is_full_sentence_rejects_fragments() -> None:
    assert extraction.is_full_sentence("너는 왜 역도를 하니?")     # has verb 하다
    assert extraction.is_full_sentence("저 괜찮으세요?")          # adjective 괜찮다
    assert extraction.is_full_sentence("이제 곧 무대다.")         # copula 다
    assert not extraction.is_full_sentence("야 역도.")           # interjection + noun, no predicate
    assert not extraction.is_full_sentence("스톱.")             # single word
    assert not extraction.is_full_sentence("음.")              # interjection


def test_pick_targets_filters_fragments() -> None:
    sents = ["야 역도.", "너는 왜 역도를 하니?", "스톱.", "저는 시장에 가요.", "음."]
    picks = extraction.pick_targets(sents, DemoLLM(), n=5)
    kos = [p.ko for p in picks]
    assert "너는 왜 역도를 하니?" in kos and "저는 시장에 가요." in kos
    assert "야 역도." not in kos and "스톱." not in kos


def test_align_index_fuzzy_fallback() -> None:
    sents = ["자전거를 타고 싶어요", "밥을 먹어요"]
    # near-match (extra spacing/particle) should still map to index 0
    assert extraction._align_index("자전거를 타고 싶어요!", 9, sents) == 0


def test_relocate_target_word_level(cache_tmp, monkeypatch) -> None:
    # probe transcript: the target words sit inside a longer run; relocate must return
    # the TIGHT span of just the target words, offset back to absolute time.
    monkeypatch.setattr(media, "extract_audio", lambda *a, **k: media.Path(a[3]))
    sents = [
        Sentence("하필 복주", 0.0, 1.2, [Word("하필", 0.0, 0.6), Word("복주", 0.6, 1.2)]),
        Sentence("저 괜찮으세요", 2.2, 3.0, [Word("저", 2.2, 2.4), Word("괜찮으세요", 2.4, 3.0)]),
    ]
    loc = avcards.relocate_target(
        FakeTranscriber(sents), media.Path("/x/src.mp4"), cache_tmp,
        "저 괜찮으세요?", approx_start=10.0, approx_end=11.0, window_s=8.0)
    assert loc is not None
    # window start = max(0,10-8)=2.0; target words at local [2.2,3.0] -> abs [4.2,5.0]
    assert abs(loc[0] - 4.2) < 0.02 and abs(loc[1] - 5.0) < 0.02


def test_pick_context_skips_fragments() -> None:
    sents = [
        Sentence("선수들이 경기장에 모였어요.", 0.0, 1.5, []),   # full sentence
        Sentence("야!", 1.6, 1.8, []),                       # fragment
        Sentence("스톱.", 1.9, 2.1, []),                      # fragment
        Sentence("너는 왜 역도를 하니?", 2.2, 3.4, []),         # target at idx 3
    ]
    ctx = avcards.pick_context(sents, 3)
    assert ctx is not None and ctx.text == "선수들이 경기장에 모였어요."   # skipped both fragments
    assert avcards.pick_context(sents, 0) is None                      # nothing before the first


def test_build_av_offline(cache_tmp) -> None:
    seg_id = _make_segment()
    res = avcards.build_av_cards(
        seg_id, llm=DemoLLM(), transcriber=FakeTranscriber(_sentences()),
        tts_provider=tts.FakeTTS(), make_media=False, push=False)
    assert len(res.targets) == 5
    assert res.pushed == 0

    # context of a non-first target = the previous Whisper sentence
    second = res.targets[1]
    assert second.context_ko == "오늘 어디 가요?"
    # spans: audio over the target, clip starts at the context sentence
    assert second.context_start_s <= second.start_s < second.end_s
    # primer audio (clean TTS) was produced and is NOT an Anki field
    assert second.primer_audio and second.audio_file == ""


def test_build_av_persists_and_primer(cache_tmp) -> None:
    seg_id = _make_segment()
    avcards.build_av_cards(seg_id, llm=DemoLLM(), transcriber=FakeTranscriber(_sentences()),
                           tts_provider=tts.FakeTTS(), make_media=False, push=False)
    primer = avcards.primer_for_segment(seg_id)
    assert len(primer) == 5
    assert all(p["target_ko"] and p["hint_en"] for p in primer)
    assert all(p["audio_path"] for p in primer)        # clean TTS path present

    # rebuilding without replace is refused (idempotency guard)
    with pytest.raises(ValueError):
        avcards.build_av_cards(seg_id, llm=DemoLLM(),
                               transcriber=FakeTranscriber(_sentences()),
                               tts_provider=tts.FakeTTS(), make_media=False, push=False)


def test_build_av_push_via_fake_transport(cache_tmp, monkeypatch) -> None:
    """Exercise the Anki push path without ffmpeg by faking media cuts + transport."""
    from pathlib import Path

    from app.anki.connect import AnkiConnectClient, FakeTransport

    def _stub_cut(src, start, end, out, **k):
        Path(out).write_bytes(b"stub")
        return Path(out)

    monkeypatch.setattr(media, "ensure_video",
                        lambda yid, url=None, section=None: media.VideoPaths(yid, cache_tmp, cache_tmp / "s.mp4", 300.0))
    monkeypatch.setattr(media, "cut_clip", _stub_cut)
    monkeypatch.setattr(media, "extract_audio", _stub_cut)

    seg_id = _make_segment()
    anki = AnkiConnectClient(transport=FakeTransport())
    res = avcards.build_av_cards(
        seg_id, llm=DemoLLM(), transcriber=FakeTranscriber(_sentences()),
        tts_provider=tts.FakeTTS(), anki=anki, make_media=True, push=True)
    assert res.pushed == 5
    assert all(t.anki_note_id for t in res.targets)
    assert all(t.audio_file.endswith("front.mp3") for t in res.targets)
