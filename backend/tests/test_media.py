"""Media command construction — pure builders, no ffmpeg/yt-dlp exec."""

from __future__ import annotations

from app.services import media


def test_ytdlp_cmd_merges_mp4() -> None:
    cmd = media.build_ytdlp_cmd("abc123XYZ_-", "/tmp/out.%(ext)s")
    # default invocation is `python -m yt_dlp` (venv package, not a PATH script)
    assert "yt_dlp" in cmd or any(str(c).endswith("yt-dlp") for c in cmd)
    assert "--merge-output-format" in cmd and "mp4" in cmd
    assert cmd[-1] == "abc123XYZ_-"
    assert "/tmp/out.%(ext)s" in cmd


def test_ffprobe_cmd_asks_for_duration_json() -> None:
    cmd = media.build_ffprobe_cmd("/v/source.mp4")
    assert "format=duration" in cmd
    assert "json" in cmd and cmd[-1] == "/v/source.mp4"


def test_audio_cmd_whisper_wav_is_16k_mono() -> None:
    cmd = media.build_ffmpeg_audio_cmd("/v/s.mp4", 1.5, 4.25, "/o/a.wav",
                                       fmt="wav", sample_rate=16000, mono=True)
    assert "-vn" in cmd
    assert cmd[cmd.index("-ss") + 1] == "1.500"
    assert cmd[cmd.index("-to") + 1] == "4.250"
    assert "16000" in cmd and "-ac" in cmd
    assert cmd[-1] == "/o/a.wav"


def test_audio_cmd_mp3_uses_lame() -> None:
    cmd = media.build_ffmpeg_audio_cmd("/v/s.mp4", 0.0, 2.0, "/o/a.mp3")
    assert "libmp3lame" in cmd


def test_clip_cmd_reencode_vs_copy() -> None:
    enc = media.build_ffmpeg_clip_cmd("/v/s.mp4", 10.0, 20.0, "/o/c.mp4", reencode=True)
    assert "libx264" in enc and "+faststart" in enc
    copy = media.build_ffmpeg_clip_cmd("/v/s.mp4", 10.0, 20.0, "/o/c.mp4", reencode=False)
    assert copy[copy.index("-c") + 1] == "copy"


def test_segment_dir_under_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(media.config, "MEDIA_CACHE_DIR", str(tmp_path))
    d = media.segment_dir("vidID123456", 3)
    assert d == tmp_path / "vidID123456" / "seg3"
