"""Simulate a real learner going through the full 6-day daily loop on one video,
using the REAL OpenAI API and the real service layer (same code the CLI/API call).

Each day:
  1. WARM-UP   - review the cards that became due today (yesterday's batch), grade SM-2
  2. EXTRACT   - extract today's ~10-min segment -> primer + 15 chunks, accept (due tomorrow)
  3. SPAR      - multi-turn deck-constrained conversation; the "learner" replies using
                 the app's own suggested_user_reply_ko (exactly what a real user would tap)
  4. HARVEST   - harvest 3-5 new chunks for tomorrow, accept

Writes a readable transcript to sim_week_report.md.
"""

from __future__ import annotations

import os
import random

os.environ["LANGAPP_DB"] = "/tmp/sim_week.db"
try:
    os.remove("/tmp/sim_week.db")
except OSError:
    pass

from app import db
from app.llm import OpenAIClient
from app.services import extraction, ingest, sparring, srs

MODEL = "gpt-4o-mini"
BASE = 739000          # arbitrary day-ordinal origin
TURNS_PER_DAY = 3
random.seed(7)

R: list[str] = []
def w(s: str = "") -> None:
    R.append(s)

def flush() -> None:
    open("sim_week_report.md", "w", encoding="utf-8").write("\n".join(R))


def grade_for(card) -> str:
    """A plausible human grading distribution (some forgetting)."""
    return random.choices(["good", "easy", "hard", "again"], weights=[62, 18, 12, 8])[0]


def main() -> None:
    db.init_db()
    client = OpenAIClient()
    summary = ingest.ingest_from_file("../youtube-to-transcript/AAWYtKPNhcA_ko.json", replace=True)
    segs = [s for s in ingest.get_segments(summary["video_id"]) if not s.is_full_review]
    w(f"# Six-day run-through — video {summary['youtube_id']} "
      f"({summary['duration_s']/60:.1f} min, {len(segs)} study days)\n")
    w(f"Model: {MODEL} (real API). Each day below is one full pass of the loop.\n")

    batch_due_today: list[str] = []     # the 15 chunks "learned" in today's warm-up
    last_primer = ""

    for day in range(1, len(segs) + 1):
        today = BASE + day
        seg = segs[day - 1]
        w(f"\n---\n\n## Day {day}  (segment {seg.id}, {seg.start_s/60:.1f}-{seg.end_s/60:.1f} min)\n")

        # 1. WARM-UP -------------------------------------------------------
        due = srs.due_cards(today_ord=today)
        w(f"### 1. Warm-up — {len(due)} card(s) due")
        if due:
            tally: dict[str, int] = {}
            for c in due:
                g = grade_for(c)
                srs.review_card(c.id, g, today_ord=today)
                tally[g] = tally.get(g, 0) + 1
            w(f"graded: {tally}")
            sample = due[0]
            sc = next((x for x in srs.list_cards() if x.id == sample.id), None)
            if sc:
                w(f"e.g. card '{sample.front_ko}' -> state={sc.state}, interval={sc.interval}d, ease={sc.ease}")
        else:
            w("(nothing due yet — this is the first day)")
        st = srs.stats(today_ord=today)
        w(f"deck now: total={st['total']}, new={st['new']}, due={st['due']}")

        # 2. EXTRACTION ----------------------------------------------------
        w("\n### 2. AI extraction (today's segment)")
        res = extraction.extract_segment(seg.id, client, model=MODEL)
        last_primer = res.primer_md
        w(f"primer: {res.primer_md[:200].strip()}...")
        w(f"chunks: {res.counts()}  warnings={res.warnings or 'none'}")
        for c in res.chunks[:3]:
            w(f"  - [{c.type}] {c.ko}  —  {c.en}")
        accepted = extraction.accept_chunks(res.chunks, segment_id=seg.id, today_ord=today)
        todays_extracted = [c.front_ko for c in accepted]
        w(f"accepted {len(accepted)} new chunks (due tomorrow, day {day+1})")

        # 3. SPARRING ------------------------------------------------------
        targets = batch_due_today or todays_extracted   # day 1 has no prior batch
        support = sorted(srs.known_surface_forms() - set(targets))
        w(f"\n### 3. Sparring — targets = today's {len(targets)} focus chunks; "
          f"{len(support)} known support words")
        sess = sparring.start_session(segment_id=seg.id)
        user_text = "안녕하세요! 오늘 같이 이야기해요."
        for turn in range(1, TURNS_PER_DAY + 1):
            sparring.record_turn(sess.id, "user", text_ko=user_text)
            resp = sparring.spar_turn(
                user_text=user_text, provider=client, targets=targets,
                support=support, scene=last_primer[:300],
                history=sparring.session_history(sess.id), model=MODEL,
            )
            sparring.record_turn(sess.id, "assistant", text_ko=resp.reply_ko,
                                 glosses=resp.glosses, targets_used=resp.targets_used)
            w(f"  [turn {turn}]")
            w(f"   learner : {user_text}")
            w(f"   partner : {resp.reply_ko}")
            w(f"   used    : {resp.targets_used or '(none)'}   "
              f"flagged-new: {resp.unexpected or '(none)'}")
            if resp.micro_feedback_en:
                w(f"   feedback: {resp.micro_feedback_en[:110]}")
            # the learner taps the app's suggested reply (just like a real user)
            user_text = resp.suggested_user_reply_ko or "네, 좋아요."

        # 4. HARVEST -------------------------------------------------------
        w("\n### 4. Harvest new chunks for tomorrow")
        harvested = sparring.harvest_chunks(
            history=sparring.session_history(sess.id), provider=client,
            known=srs.known_surface_forms(), model=MODEL)
        hcards = extraction.accept_chunks(harvested, today_ord=today)
        w(f"harvested {len(harvested)}: " + ", ".join(c.ko for c in harvested))

        # tomorrow's warm-up batch = everything accepted today
        batch_due_today = todays_extracted + [c.front_ko for c in hcards]
        flush()

    # Day 7 note + final summary
    w(f"\n---\n\n## Day {len(segs)+1} — FULL REWATCH (UI only)")
    w("The final day plays the whole video with the study overlay; no new extraction.")
    final = srs.stats(today_ord=BASE + len(segs) + 1)
    allc = srs.list_cards()
    w(f"\n## Final deck: {final['total']} cards "
      f"({sum(1 for c in allc if c.state=='review')} in review, "
      f"{sum(1 for c in allc if c.state=='new')} still new)")
    flush()
    print("done")


if __name__ == "__main__":
    main()
