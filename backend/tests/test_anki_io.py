"""Anki TXT interop tests — round-trip, quoting, and the content-only guarantee."""

from __future__ import annotations

from app.services import anki_io, srs


def test_export_has_header_and_no_scheduling(tmp_path) -> None:
    srs.add_card(front_ko="가다", back_en="to go", gloss="go", due_ord=1000, state="new")
    c = srs.add_card(front_ko="먹다", back_en="to eat", gloss="eat", due_ord=1000)
    srs.review_card(c.id, "easy", today_ord=1000)  # give it rich SRS state

    out = tmp_path / "deck.txt"
    n = anki_io.export_txt(out)
    assert n == 2
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#separator:;")
    assert "#columns:Korean;English;Gloss" in text
    # content present
    assert "가다;to go;go" in text
    # NO scheduling/deck-state leaked: no ease(2.65), interval, due ordinals
    assert "2.65" not in text
    assert "1004" not in text
    assert "review" not in text


def test_round_trip(tmp_path) -> None:
    srs.add_card(front_ko="물", back_en="water", gloss="water", due_ord=1000)
    srs.add_card(front_ko="불", back_en="fire", gloss="fire", due_ord=1000)
    out = tmp_path / "deck.txt"
    anki_io.export_txt(out)

    # import into a different deck and compare content
    res = anki_io.import_txt(out, deck_name="Imported")
    assert res["created"] == 2
    imported = {(c.front_ko, c.back_en, c.gloss) for c in srs.list_cards(deck_name="Imported")}
    assert imported == {("물", "water", "water"), ("불", "fire", "fire")}
    # imported cards are unscheduled by default (known vocab, not in review queue)
    assert all(c.due_ord is None for c in srs.list_cards(deck_name="Imported"))


def test_quoting_special_chars(tmp_path) -> None:
    srs.add_card(front_ko="안녕하세요; 반가워요", back_en='he said "hi"',
                 gloss="line1\nline2", due_ord=1000)
    out = tmp_path / "deck.txt"
    anki_io.export_txt(out)
    res = anki_io.import_txt(out, deck_name="RT")
    assert res["created"] == 1
    card = srs.list_cards(deck_name="RT")[0]
    assert card.front_ko == "안녕하세요; 반가워요"
    assert card.back_en == 'he said "hi"'
    assert card.gloss == "line1\nline2"


def test_import_handwritten_anki_file(tmp_path) -> None:
    f = tmp_path / "hand.txt"
    f.write_text(
        "#separator:;\n#html:false\n"
        "사과;apple;a fruit\n"
        "바나나;banana;\n"
        ";should-skip-no-korean;x\n",
        encoding="utf-8",
    )
    res = anki_io.import_txt(f, deck_name="Hand")
    assert res["created"] == 2
    assert res["skipped"] == 1


def test_import_schedule_now(tmp_path) -> None:
    f = tmp_path / "h.txt"
    f.write_text("#separator:;\n학교;school;school\n", encoding="utf-8")
    anki_io.import_txt(f, deck_name="S", schedule_now=True)
    assert len(srs.due_cards(deck_name="S", today_ord=srs.today_ordinal())) == 1
