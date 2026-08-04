"""Sentence grouping (pure) + the whisper cache round-trip."""

from __future__ import annotations

from app import config
from app.services import transcribe
from app.services.transcribe import Sentence, Word, group_sentences


def W(text, a, b):
    return Word(text=text, start_s=a, end_s=b)


def test_group_breaks_on_korean_ender_then_pause() -> None:
    words = [W("오늘", 0.0, 0.4), W("가요", 0.4, 0.9),   # ender 요 + pause
             W("저는", 1.8, 2.1), W("시장에", 2.1, 2.6), W("가요", 2.6, 3.0)]
    sents = group_sentences(words, min_gap_s=0.6)
    assert len(sents) == 2
    assert sents[0].text.replace(" ", "") == "오늘가요"
    assert sents[0].start_s == 0.0 and sents[0].end_s == 0.9
    assert sents[1].start_s == 1.8 and sents[1].end_s == 3.0


def test_group_breaks_on_terminal_punctuation() -> None:
    words = [W("뭐", 0.0, 0.3), W("해요?", 0.3, 0.8), W("네", 0.85, 1.1)]
    sents = group_sentences(words, min_gap_s=0.6)
    assert len(sents) == 2
    assert sents[0].text.endswith("?")


def test_group_no_false_split_without_pause() -> None:
    # ender 요 but the next word follows immediately (no pause) -> stay one sentence
    words = [W("가요", 0.0, 0.4), W("그리고", 0.45, 0.8), W("와요", 0.8, 1.2)]
    sents = group_sentences(words, min_gap_s=0.6)
    assert len(sents) == 1


def test_group_splits_long_runs() -> None:
    words = [W("말", i * 1.0, i * 1.0 + 0.9) for i in range(20)]  # no punctuation
    sents = group_sentences(words, min_gap_s=0.05, max_len_s=5.0)
    assert len(sents) >= 3


def test_group_force_splits_continuous_speech() -> None:
    """The bug that produced a 52-second card target: every break rule needed a pause,
    and unbroken cartoon dialogue never offers one."""
    words = [W("말", i * 0.5, i * 0.5 + 0.5) for i in range(120)]   # 60s, zero gaps
    sents = group_sentences(words, min_gap_s=0.6, max_len_s=8.0, hard_max_len_s=12.0)
    assert sents, "must not swallow the whole run"
    assert max(s.end_s - s.start_s for s in sents) <= 12.0
    # every word survives the split
    assert sum(len(s.words) for s in sents) == 120


def test_force_split_prefers_the_widest_silence() -> None:
    # 0.4s pause after word 9 — under min_gap_s, so only the hard cap can use it
    words = []
    for i in range(20):
        start = i * 0.7 + (0.4 if i >= 10 else 0.0)
        words.append(W("말", start, start + 0.7))
    sents = group_sentences(words, min_gap_s=0.6, max_len_s=8.0, hard_max_len_s=10.0)
    assert sents[0].end_s == words[9].end_s


def test_group_does_not_force_split_short_runs() -> None:
    words = [W("가", 0.0, 0.4), W("자", 0.4, 0.9)]
    assert len(group_sentences(words, hard_max_len_s=12.0)) == 1


def test_load_cached_regroups_from_words(monkeypatch, tmp_path) -> None:
    """Cached transcripts hold Whisper's words, so a grouping fix applies to segments
    already transcribed rather than needing an expensive re-run."""
    from app.services import media
    monkeypatch.setattr(media.config, "MEDIA_CACHE_DIR", str(tmp_path))
    blob = [W("말", i * 0.5, i * 0.5 + 0.5) for i in range(120)]
    transcribe.save_cache("vid12345678", 1, [Sentence("말" * 120, 0.0, 60.0, blob)])
    loaded = transcribe.load_cached("vid12345678", 1)
    assert loaded is not None and len(loaded) > 1
    assert max(s.end_s - s.start_s for s in loaded) <= config.WHISPER_SENT_HARD_MAX_S


def test_cache_round_trip(monkeypatch, tmp_path) -> None:
    from app.services import media
    monkeypatch.setattr(media.config, "MEDIA_CACHE_DIR", str(tmp_path))
    # words must spell out the text: load_cached re-groups from them, and that is
    # lossless only because group_sentences is what wrote the text in the first place
    sents = [Sentence("오늘 가요", 0.0, 0.9, [W("오늘 ", 0.0, 0.4), W("가요", 0.4, 0.9)])]
    transcribe.save_cache("vid12345678", 1, sents)
    loaded = transcribe.load_cached("vid12345678", 1)
    assert loaded is not None and len(loaded) == 1
    assert loaded[0].text == "오늘 가요"
    assert loaded[0].words[0].text.strip() == "오늘"


def test_fake_transcriber_returns_sentences() -> None:
    out = transcribe.FakeTranscriber().transcribe("/nope/missing.wav")
    assert len(out) >= 5 and all(s.text for s in out)
