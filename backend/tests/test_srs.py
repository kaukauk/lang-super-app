"""SM-2 scheduler tests — pure logic + the Card service layer with simulated days."""

from __future__ import annotations

import pytest

from app.services import srs


def sched(interval, ease, reps, lapses, grade, today=1000):
    return srs.schedule(interval=interval, ease=ease, reps=reps,
                        lapses=lapses, grade=grade, today_ord=today)


def test_new_card_first_grades() -> None:
    assert sched(0, 2.5, 0, 0, "good")["interval"] == 1
    assert sched(0, 2.5, 0, 0, "easy")["interval"] == 4
    assert sched(0, 2.5, 0, 0, "hard")["interval"] == 1


def test_good_progression_classic_sm2() -> None:
    s1 = sched(0, 2.5, 0, 0, "good")          # reps0 -> 1 day
    assert (s1["interval"], s1["reps"]) == (1, 1)
    s2 = sched(s1["interval"], s1["ease"], s1["reps"], 0, "good")  # reps1 -> 6 days
    assert (s2["interval"], s2["reps"]) == (6, 2)
    s3 = sched(s2["interval"], s2["ease"], s2["reps"], 0, "good")  # 6 * 2.5
    assert s3["interval"] == 15
    assert s3["due_ord"] == 1000 + 15


def test_again_resets_and_drops_ease() -> None:
    s = sched(15, 2.5, 3, 0, "again")
    assert s["reps"] == 0
    assert s["interval"] == 1
    assert s["lapses"] == 1
    assert s["ease"] == pytest.approx(2.3)


def test_ease_floor() -> None:
    ease = 1.35
    for _ in range(5):
        ease = sched(10, ease, 3, 0, "again")["ease"]
    assert ease == pytest.approx(srs.EASE_MIN)


def test_hard_and_easy_adjust_ease() -> None:
    assert sched(10, 2.5, 3, 0, "hard")["ease"] == pytest.approx(2.35)
    assert sched(10, 2.5, 3, 0, "easy")["ease"] == pytest.approx(2.65)


def test_invalid_grade() -> None:
    with pytest.raises(ValueError):
        sched(0, 2.5, 0, 0, "meh")


# --- service layer ---

def test_add_and_review_card_persists() -> None:
    card = srs.add_card(front_ko="가다", back_en="to go", gloss="go",
                        surface_forms=["가다"], due_ord=1000, state="new")
    assert card.state == "new"
    updated = srs.review_card(card.id, "good", today_ord=1000)
    assert updated.state == "review"
    assert updated.interval == 1
    assert updated.due_ord == 1001


def test_due_cards_respects_today() -> None:
    srs.add_card(front_ko="오늘", due_ord=1000, state="new")
    srs.add_card(front_ko="내일", due_ord=1005, state="new")
    assert len(srs.due_cards(today_ord=999)) == 0
    assert len(srs.due_cards(today_ord=1000)) == 1
    assert len(srs.due_cards(today_ord=1005)) == 2


def test_review_advances_due_out_of_window() -> None:
    c = srs.add_card(front_ko="단어", due_ord=1000, state="new")
    srs.review_card(c.id, "easy", today_ord=1000)        # interval 4 -> due 1004
    assert len(srs.due_cards(today_ord=1000)) == 0
    assert len(srs.due_cards(today_ord=1004)) == 1


def test_stats_and_known_forms() -> None:
    srs.add_card(front_ko="물", surface_forms=["물"], due_ord=1000, state="new")
    c = srs.add_card(front_ko="불", surface_forms=["불"], due_ord=1000, state="new")
    srs.review_card(c.id, "good", today_ord=1000)
    st = srs.stats(today_ord=1000)
    # count only: Anki owns scheduling, so a new/due split here would be the app
    # inventing numbers the collection is the authority on
    assert st == {"total": 2}
    # known vocabulary comes from Anki now, not this table — proved with a fake
    # transport rather than whatever collection happens to be open
    from app.anki.connect import AnkiConnectClient, FakeTransport
    from app.services import known
    ft = FakeTransport(results={
        "findNotes": [1, 2],
        "notesInfo": [{"fields": {"Front": {"value": "물"}, "Back": {"value": "water"}}},
                      {"fields": {"Front": {"value": "불"}, "Back": {"value": "fire"}}}],
    })
    forms = known.known_surface_forms("Korean", anki=AnkiConnectClient(transport=ft))
    assert {"물", "불"} <= forms
    assert "water" not in forms          # English fields are not surface forms
