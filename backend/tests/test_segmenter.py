"""Unit tests for the segmenter — the piece that must guarantee 9-11 min
segments with no tiny tail, on both synthetic and real transcripts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.segmenter import (
    Cue,
    choose_count,
    plan_segments,
    total_duration,
)

MIN_S, MAX_S = 540, 660


def contiguous_cues(T: float, step: float = 2.0) -> list[Cue]:
    """A dense, gap-free transcript so boundaries may fall anywhere."""
    cues: list[Cue] = []
    t = 0.0
    while t < T:
        end = min(t + step, T)
        cues.append((t, end))
        t = end
    return cues


@pytest.mark.parametrize(
    "minutes,expected",
    [(50, 5), (55, 6), (60, 6), (63, 6), (66, 7), (67, 7), (75, 8), (95, 10)],
)
def test_choose_count(minutes: int, expected: int) -> None:
    assert choose_count(minutes * 60) == expected


def _feasible(T: float) -> bool:
    import math
    return math.ceil(T / MAX_S) <= math.floor(T / MIN_S)


@pytest.mark.parametrize("minutes", [9, 10, 11, 20, 50, 61, 63, 75, 88, 95, 120])
def test_every_segment_in_band_and_no_tiny_tail(minutes: int) -> None:
    T = minutes * 60.0
    cues = contiguous_cues(T)
    plans = plan_segments(T, cues)
    study = [p for p in plans if not p.is_full_review]
    full = [p for p in plans if p.is_full_review]

    assert len(full) == 1
    assert full[0].start_s == 0.0 and full[0].end_s == pytest.approx(T)

    # contiguous, covering, increasing
    assert study[0].start_s == 0.0
    assert study[-1].end_s == pytest.approx(T)
    for a, b in zip(study, study[1:]):
        assert b.start_s == pytest.approx(a.end_s)

    # every study segment within [9, 11] min (unless the whole video is shorter)
    for p in study:
        if T > MAX_S:
            assert MIN_S - 1e-6 <= p.length_s <= MAX_S + 1e-6, (minutes, p)
    # no tiny tail
    if T > MAX_S:
        assert study[-1].length_s >= MIN_S - 1e-6


@pytest.mark.parametrize("minutes", [35, 23])
def test_infeasible_duration_is_graceful(minutes: int) -> None:
    """Durations that can't be split into all-[9,11] segments must still produce
    contiguous, covering segments with no tiny tail (close to the band)."""
    T = minutes * 60.0
    assert not _feasible(T)
    study = [p for p in plan_segments(T, contiguous_cues(T)) if not p.is_full_review]
    assert study[0].start_s == 0.0
    assert study[-1].end_s == pytest.approx(T)
    for a, b in zip(study, study[1:]):
        assert b.start_s == pytest.approx(a.end_s)
    for p in study:
        assert p.length_s > 0
        assert p.length_s <= MAX_S * 1.25          # never wildly long
    assert study[-1].length_s >= MIN_S * 0.75       # no tiny tail


def test_day_numbering() -> None:
    T = 3600.0
    plans = plan_segments(T, contiguous_cues(T))
    assert [p.day_number for p in plans] == [1, 2, 3, 4, 5, 6, 7]
    assert plans[-1].is_full_review is True
    assert plans[-1].day_number == 7


def test_snaps_to_real_gap() -> None:
    # Two ~10-min halves with a big silence gap near the midpoint of a 20-min video.
    T = 1200.0
    cues: list[Cue] = []
    t = 0.0
    while t < 595:
        cues.append((t, t + 5)); t += 5
    # silence from 595..610, then resume
    t = 610.0
    while t < T:
        cues.append((t, min(t + 5, T))); t += 5
    plans = [p for p in plan_segments(T, cues) if not p.is_full_review]
    # 20 min -> 2 segments; boundary should snap to the cue starting at 610
    assert len(plans) == 2
    assert plans[0].end_s == pytest.approx(610.0)


def test_real_sample_transcript() -> None:
    sample = (
        Path(__file__).resolve().parents[2]
        / "youtube-to-transcript"
        / "AAWYtKPNhcA_ko.json"
    )
    if not sample.exists():
        pytest.skip("sample transcript not present")
    raw = json.loads(sample.read_text(encoding="utf-8"))
    cues: list[Cue] = [(s["start"], s["start"] + s["duration"]) for s in raw]
    T = total_duration(cues)
    plans = plan_segments(T, cues)
    study = [p for p in plans if not p.is_full_review]
    # ~61-64 min video -> 6 study days + 1 full day
    assert len(study) == 6
    assert sum(1 for p in plans if p.is_full_review) == 1
    for p in study:
        assert MIN_S - 1e-6 <= p.length_s <= MAX_S + 1e-6, p
    # boundaries should align to actual cue starts (no mid-cue cuts)
    cue_starts = {round(s, 3) for s, _e in cues}
    for p in study[1:]:
        assert round(p.start_s, 3) in cue_starts
