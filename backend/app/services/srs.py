"""Our own SM-2 spaced-repetition scheduler.

`schedule()` is a pure function (state in -> state out) so it is exhaustively
unit-testable; the service functions apply it to Card rows. Scheduling is
day-granular and uses date ordinals so tests can fast-forward time.

SRS state is INTERNAL ONLY — it is never written to / read from the Anki TXT
import-export (see anki_io.py).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from sqlmodel import func, select

from .. import db
from ..models import Card, Deck

GRADES = ("again", "hard", "good", "easy")
EASE_MIN = 1.3
EASE_DEFAULT = 2.5


def today_ordinal() -> int:
    return date.today().toordinal()


def schedule(
    *, interval: int, ease: float, reps: int, lapses: int, grade: str, today_ord: int,
) -> dict[str, Any]:
    """Apply one SM-2 review. Returns the new scheduling fields."""
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}, got {grade!r}")

    if grade == "again":
        reps = 0
        lapses += 1
        ease = max(EASE_MIN, ease - 0.20)
        interval = 1
    else:
        if reps == 0:
            interval = {"hard": 1, "good": 1, "easy": 4}[grade]
        elif reps == 1 and grade == "good":
            interval = 6
        else:
            mult = {"hard": 1.2, "good": ease, "easy": ease * 1.3}[grade]
            interval = max(1, round(interval * mult))
        if grade == "hard":
            ease = max(EASE_MIN, ease - 0.15)
        elif grade == "easy":
            ease = ease + 0.15
        reps += 1

    return {
        "state": "review",
        "interval": int(interval),
        "ease": round(float(ease), 4),
        "reps": int(reps),
        "lapses": int(lapses),
        "due_ord": today_ord + int(interval),
        "last_reviewed_ord": today_ord,
    }


# --- service layer -------------------------------------------------------

def ensure_deck(name: str = "Korean") -> Deck:
    with db.session_scope() as s:
        deck = s.exec(select(Deck).where(Deck.name == name)).first()
        if deck is None:
            deck = Deck(name=name)
            s.add(deck)
            s.flush()
        return deck


def add_card(
    *, front_ko: str, back_en: str = "", gloss: str = "", kind: str = "manual",
    surface_forms: Optional[list[str]] = None, source_segment_id: Optional[int] = None,
    deck_name: str = "Korean", due_ord: Optional[int] = None, state: str = "new",
) -> Card:
    deck = ensure_deck(deck_name)
    with db.session_scope() as s:
        card = Card(
            deck_id=deck.id, kind=kind, front_ko=front_ko, back_en=back_en, gloss=gloss,
            surface_forms=json.dumps(surface_forms or [], ensure_ascii=False),
            source_segment_id=source_segment_id,
            state=state, due_ord=due_ord, ease=EASE_DEFAULT,
        )
        s.add(card)
        s.flush()
        return card


def review_card(card_id: int, grade: str, *, today_ord: Optional[int] = None) -> Card:
    today = today_ord if today_ord is not None else today_ordinal()
    with db.session_scope() as s:
        card = s.get(Card, card_id)
        if card is None:
            raise ValueError(f"no card {card_id}")
        upd = schedule(
            interval=card.interval, ease=card.ease, reps=card.reps,
            lapses=card.lapses, grade=grade, today_ord=today,
        )
        for k, v in upd.items():
            setattr(card, k, v)
        s.add(card)
        s.flush()
        return card


def due_cards(
    *, deck_name: str = "Korean", today_ord: Optional[int] = None, limit: Optional[int] = None,
) -> list[Card]:
    """Cards whose due date has arrived (covers both never-studied 'new' cards and
    review cards). Cards with due_ord=None are not yet scheduled."""
    today = today_ord if today_ord is not None else today_ordinal()
    deck = ensure_deck(deck_name)
    with db.session_scope() as s:
        q = (
            select(Card)
            .where(Card.deck_id == deck.id)
            .where(Card.due_ord != None)  # noqa: E711
            .where(Card.due_ord <= today)
            .order_by(Card.due_ord, Card.id)
        )
        if limit:
            q = q.limit(limit)
        return list(s.exec(q).all())


def stats(*, deck_name: str = "Korean", today_ord: Optional[int] = None) -> dict[str, int]:
    today = today_ord if today_ord is not None else today_ordinal()
    deck = ensure_deck(deck_name)
    with db.session_scope() as s:
        total = s.exec(
            select(func.count()).select_from(Card).where(Card.deck_id == deck.id)
        ).one()
        new = s.exec(
            select(func.count()).select_from(Card)
            .where(Card.deck_id == deck.id).where(Card.state == "new")
        ).one()
        due = s.exec(
            select(func.count()).select_from(Card)
            .where(Card.deck_id == deck.id)
            .where(Card.due_ord != None).where(Card.due_ord <= today)  # noqa: E711
        ).one()
    return {"total": int(total), "new": int(new), "due": int(due)}


def list_cards(*, deck_name: str = "Korean") -> list[Card]:
    deck = ensure_deck(deck_name)
    with db.session_scope() as s:
        return list(s.exec(
            select(Card).where(Card.deck_id == deck.id).order_by(Card.id)
        ).all())


def known_surface_forms(*, deck_name: str = "Korean") -> set[str]:
    """All surface forms across the deck (for extraction dedupe + sparring vocab)."""
    forms: set[str] = set()
    for c in list_cards(deck_name=deck_name):
        try:
            forms.update(json.loads(c.surface_forms))
        except (json.JSONDecodeError, TypeError):
            pass
        if c.front_ko:
            forms.add(c.front_ko.strip())
    return {f for f in forms if f}
