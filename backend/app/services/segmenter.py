"""Split a video of total duration T into study segments of a fixed LENGTH (not a
fixed count): the [SEG_MIN_S, SEG_MAX_S] band in config, currently 4:45-5:15,
snapped to gaps between transcript cues so dialogue is never cut mid-sentence,
with NO tiny leftover tail (the bug in the old section.py fixed-wall approach).

The number of segments is chosen so that an even split already lands every
segment inside [SEG_MIN_S, SEG_MAX_S]; snapping then nudges each boundary onto a
natural pause without leaving that band. A final full-rewatch "day" spanning the
whole video is appended.

All functions are pure (no DB) so they are trivially unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .. import config

Cue = tuple[float, float]  # (start_s, end_s)


@dataclass(frozen=True)
class SegmentPlan:
    index: int          # 0-based
    day_number: int     # 1-based
    start_s: float
    end_s: float
    is_full_review: bool

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


def choose_count(
    T: float,
    min_s: int = config.SEG_MIN_S,
    max_s: int = config.SEG_MAX_S,
    target_s: int = config.SEG_TARGET_S,
) -> int:
    """Pick the segment count whose even split sits in [min_s, max_s], closest to
    T/target_s (ties prefer MORE, shorter segments)."""
    if T <= max_s:
        return 1
    n_lo = math.ceil(T / max_s)
    n_hi = math.floor(T / min_s)
    if n_lo <= n_hi and n_hi >= 1:
        ideal = T / target_s
        # min by (distance to ideal, then larger n) -> tie prefers larger n
        return min(range(max(1, n_lo), n_hi + 1), key=lambda n: (abs(n - ideal), -n))

    # Infeasible: no count keeps every segment in [min_s, max_s] (e.g. ~35 min).
    # Choose between "too few/too long" (n_hi) and "too many/too short" (n_lo)
    # whichever deviates least from the band.
    def band_deviation(n: int) -> float:
        length = T / n
        return max(0.0, min_s - length, length - max_s)

    candidates = [c for c in (n_lo, n_hi) if c >= 1] or [max(1, round(T / target_s))]
    return min(candidates, key=lambda n: (band_deviation(n), -n))


def _boundary_candidates(cues: list[Cue], T: float) -> list[tuple[float, float]]:
    """Return (gap_size, boundary_pos) for each inter-cue gap. boundary_pos is the
    start of the following cue (so a new segment begins exactly on a cue)."""
    cands: list[tuple[float, float]] = []
    ordered = sorted(cues, key=lambda c: c[0])
    for (s0, e0), (s1, _e1) in zip(ordered, ordered[1:]):
        if 0.0 < s1 < T:
            cands.append((max(0.0, s1 - e0), s1))
    return cands


def _snap(
    c: float, cands: list[tuple[float, float]], prev: float,
    *, min_s: int, max_s: int, window_s: int, T: float, remaining: int,
) -> float:
    """Snap ideal cut `c` to the largest nearby gap while keeping the segment that
    starts at `prev` within [min_s, max_s] and leaving feasible room for the
    `remaining` segments after this boundary."""
    lo = max(prev + min_s, T - remaining * max_s)
    hi = min(prev + max_s, T - remaining * min_s)
    if hi < lo:                       # numerical safety; shouldn't happen for feasible n
        lo, hi = prev + min_s, prev + max_s
    in_window = [(g, b) for (g, b) in cands if abs(b - c) <= window_s and lo <= b <= hi]
    if in_window:
        # biggest gap first, then nearest to the ideal cut
        in_window.sort(key=lambda gb: (-gb[0], abs(gb[1] - c)))
        return in_window[0][1]
    target = min(max(c, lo), hi)
    in_range = [b for (_g, b) in cands if lo <= b <= hi]
    if in_range:
        return min(in_range, key=lambda b: abs(b - target))
    return target                     # no cue boundary available -> clean even cut


def plan_segments(
    T: float,
    cues: list[Cue],
    *,
    min_s: int = config.SEG_MIN_S,
    max_s: int = config.SEG_MAX_S,
    target_s: int = config.SEG_TARGET_S,
    window_s: int = config.SEG_SNAP_WINDOW_S,
) -> list[SegmentPlan]:
    """Return study segments (days 1..n) plus a final full-rewatch day (n+1)."""
    if T <= 0:
        raise ValueError("video duration must be positive")
    n = choose_count(T, min_s, max_s, target_s)
    cands = _boundary_candidates(cues, T)

    boundaries: list[float] = []
    prev = 0.0
    for i in range(1, n):
        c = i * T / n
        b = _snap(c, cands, prev, min_s=min_s, max_s=max_s,
                  window_s=window_s, T=T, remaining=n - i)
        b = max(b, prev + 1.0)        # strictly increasing
        boundaries.append(b)
        prev = b

    points = [0.0, *boundaries, T]
    plans: list[SegmentPlan] = [
        SegmentPlan(index=i, day_number=i + 1, start_s=a, end_s=b, is_full_review=False)
        for i, (a, b) in enumerate(zip(points, points[1:]))
    ]
    plans.append(
        SegmentPlan(index=len(plans), day_number=len(plans) + 1,
                    start_s=0.0, end_s=T, is_full_review=True)
    )
    return plans


def total_duration(cues: list[Cue]) -> float:
    """T = end time of the last cue (the transcript API gives no video duration)."""
    return max((e for _s, e in cues), default=0.0)
