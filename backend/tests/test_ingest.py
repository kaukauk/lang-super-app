"""Ingest tests — deterministic, from the local sample JSON (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.services import ingest

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "youtube-to-transcript" / "AAWYtKPNhcA_ko.json"
)


def test_extract_video_id() -> None:
    assert ingest.extract_video_id("AAWYtKPNhcA") == "AAWYtKPNhcA"
    assert ingest.extract_video_id("https://youtu.be/AAWYtKPNhcA") == "AAWYtKPNhcA"
    assert ingest.extract_video_id("https://www.youtube.com/watch?v=AAWYtKPNhcA") == "AAWYtKPNhcA"
    with pytest.raises(ValueError):
        ingest.extract_video_id("not a video")


def test_ingest_from_synthetic_cues() -> None:
    cues = [{"text": f"line {i}", "start": i * 2.0, "duration": 2.0} for i in range(450)]
    summary = ingest.ingest_cues("test12345AB", cues, title="Synthetic")
    assert summary["n_cues"] == 450
    assert summary["duration_s"] == pytest.approx(900.0)
    assert summary["n_study_segments"] >= 1
    assert summary["n_days"] == summary["n_study_segments"] + 1

    segs = ingest.get_segments(summary["video_id"])
    assert segs[-1].is_full_review is True
    text = ingest.segment_text(segs[0].id)
    assert text.startswith("line 0")


def test_reingest_requires_replace() -> None:
    cues = [{"text": "a", "start": 0.0, "duration": 1.0}]
    ingest.ingest_cues("dupvideoXYZ", cues, title="t")
    with pytest.raises(ValueError):
        ingest.ingest_cues("dupvideoXYZ", cues, title="t")
    # replace works and doesn't duplicate
    ingest.ingest_cues("dupvideoXYZ", cues, title="t2", replace=True)
    assert len(ingest.list_videos()) == 1


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample transcript missing")
def test_ingest_real_sample() -> None:
    summary = ingest.ingest_from_file(SAMPLE)
    assert summary["youtube_id"] == "AAWYtKPNhcA"
    assert summary["n_cues"] == 1127
    assert summary["n_study_segments"] == 12
    assert 3600 < summary["duration_s"] < 3720

    segs = ingest.get_segments(summary["video_id"])
    study = [s for s in segs if not s.is_full_review]
    for s in study:
        assert config.SEG_MIN_S - 1 <= (s.end_s - s.start_s) <= config.SEG_MAX_S + 1
    # cues route correctly into segment 1
    cues1 = ingest.get_cues_in_range(summary["video_id"], study[0].start_s, study[0].end_s)
    assert len(cues1) > 50
