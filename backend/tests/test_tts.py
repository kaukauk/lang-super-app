"""TTS caching + the offline FakeTTS stub (no network)."""

from __future__ import annotations

from app.services import tts


def test_fake_tts_writes_real_file(tmp_path) -> None:
    out = tts.FakeTTS().synth("오늘 가요", tmp_path / "a.mp3")
    assert out.exists() and out.stat().st_size > 0


def test_synth_cached_reuses_existing(tmp_path) -> None:
    p1 = tts.synth_cached("자전거를 타고 싶어요", tmp_path, provider=tts.FakeTTS())
    mtime = p1.stat().st_mtime_ns
    p2 = tts.synth_cached("자전거를 타고 싶어요", tmp_path, provider=tts.FakeTTS())
    assert p1 == p2
    assert p2.stat().st_mtime_ns == mtime           # not rewritten


def test_synth_cached_distinct_text_distinct_file(tmp_path) -> None:
    a = tts.synth_cached("가요", tmp_path, provider=tts.FakeTTS())
    b = tts.synth_cached("와요", tmp_path, provider=tts.FakeTTS())
    assert a != b


def test_key_is_voice_sensitive() -> None:
    assert tts._key("가요", "voiceA") != tts._key("가요", "voiceB")
