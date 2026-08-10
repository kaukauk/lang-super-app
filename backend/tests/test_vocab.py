"""Word-level subtitle annotation: what gets highlighted, what colour, what gloss."""

from __future__ import annotations

from app import config
from app.anki.connect import AnkiConnectClient, FakeTransport
from app.services import vocab


def fake_anki(notes: list[dict]) -> AnkiConnectClient:
    """An Anki holding `notes`, each given as {field name: value}."""
    return AnkiConnectClient(transport=FakeTransport({
        "findNotes": list(range(len(notes))),
        "notesInfo": [{"noteId": i, "fields": {k: {"value": v} for k, v in n.items()}}
                      for i, n in enumerate(notes)],
    }))


def _known(*forms: str) -> AnkiConnectClient:
    """A deck holding exactly these forms, so a test can say which words are the
    unknown ones."""
    return fake_anki([{"Front": f, "Back": f"gloss of {f}"} for f in forms])


def surfaces(text: str) -> list[str]:
    return [t.surface for t in vocab.tokenize(text)]


# --- what is a word ------------------------------------------------------

def test_particles_are_not_words() -> None:
    """The whole reason POS tagging is doing this: 을 is grammar, 물 is vocabulary."""
    assert surfaces("물을 마셔요") == ["물", "마셔요"]


def test_a_predicate_is_highlighted_whole() -> None:
    """Highlighting the bare stem out of 먹었어요 would read as a typo, and the
    conjugation is the part the learner is looking at."""
    assert surfaces("밥을 먹었어요") == ["밥", "먹었어요"]


def test_noun_plus_hada_is_one_verb() -> None:
    toks = vocab.tokenize("한국어를 공부했어요")
    assert [t.surface for t in toks] == ["한국어", "공부했어요"]
    assert toks[-1].lemma == "공부하다" and toks[-1].pos == "verb"


def test_punctuation_stays_outside_the_highlight() -> None:
    assert surfaces("예뻐요!") == ["예뻐요"]
    assert surfaces("달라졌어요.") == ["달라졌어요"]


def test_spans_are_ordered_and_do_not_overlap() -> None:
    toks = vocab.tokenize("하지만 불의 제국이 전쟁을 일으켰고 모든 것이 달라졌어요.")
    assert toks == sorted(toks, key=lambda t: t.start)
    assert all(a.end <= b.start for a, b in zip(toks, toks[1:]))
    # the span must be exactly what it claims, or the overlay highlights the wrong
    # characters
    text = "하지만 불의 제국이 전쟁을 일으켰고 모든 것이 달라졌어요."
    assert all(text[t.start:t.end] == t.surface for t in toks)


def test_empty_line_is_not_a_crash() -> None:
    assert vocab.tokenize("") == [] and vocab.tokenize("   ") == []


# --- the form shown ------------------------------------------------------

def test_verbs_display_their_polite_form() -> None:
    """The deck teaches 아요/어요, so that is the form worth showing and producing."""
    assert vocab.tokenize("밥을 먹었어요")[-1].display == "먹어요"


def test_irregulars_conjugate_correctly() -> None:
    """Kiwi's own conjugator, which is the point of not hardcoding a table: ㄷ and ㅂ
    irregulars come out right."""
    assert vocab.polite_form("듣다", "VV-I") == "들어요"
    assert vocab.polite_form("돕다", "VV-I") == "도와요"
    assert vocab.polite_form("예쁘다", "VA") == "예뻐요"


def test_nouns_display_themselves() -> None:
    assert vocab.tokenize("물을 마셔요")[0].display == "물"


# --- colour --------------------------------------------------------------

def test_exact_form_in_the_deck_is_known() -> None:
    anki = fake_anki([{"Front": "물", "Back": "water"}])
    tok = vocab.annotate(["물을 마셔요"], translate=False, anki=anki)[0].tokens[0]
    assert tok.status == "known" and tok.gloss_en == "water"


def test_other_form_of_a_known_word_is_derived() -> None:
    """The case worth seeing while watching: 먹어요 is a card, 먹었어요 has never been
    met. Calling that green would hide the only interesting thing on the line."""
    anki = fake_anki([{"Front": "먹어요", "Back": "to eat"}])
    tok = vocab.annotate(["밥을 먹었어요"], translate=False, anki=anki)[0].tokens[-1]
    assert tok.status == "derived" and tok.gloss_en == "to eat"


def test_unseen_word_is_new() -> None:
    anki = fake_anki([{"Front": "물", "Back": "water"}])
    tok = vocab.annotate(["제국이"], translate=False, anki=anki)[0].tokens[0]
    assert tok.status == "new" and tok.gloss_en == ""


def test_a_sentence_note_does_not_gloss_its_every_word() -> None:
    """A note whose Korean side is a whole sentence would otherwise attach that
    sentence's translation to each content word in it."""
    anki = fake_anki([{"Front": "밥을 먹었어요", "Back": "I ate a meal"}])
    index = vocab.build_index(anki=anki)
    assert index["lemmas"] == {}
    assert "밥을 먹었어요" in index["forms"]


def test_a_mined_cloze_makes_its_own_word_known() -> None:
    """Without this the feature works against itself: a noun mined as a cloze
    contributes only the sentence, so the word stays new forever — and keeps
    blocking its neighbours' clozes forever with it. The bolded span on the back is
    the note saying which word it is about."""
    anki = fake_anki([{"Chunk": "하지만 제국이 컸어요",
                       "Answer": "하지만 <b>제국이</b> 컸어요", "Gloss": "empire"}])
    vocab.invalidate_index()
    tok = vocab.annotate(["제국이 왔어요"], translate=False, anki=anki)[0].tokens[0]
    assert tok.status == "known" and tok.gloss_en == "empire"
    # and the same noun with a different particle is the derived case, not a miss
    other = vocab.annotate(["제국은 왔어요"], translate=False, anki=anki)[0].tokens[0]
    assert other.status == "derived"
    vocab.invalidate_index()


def test_only_cards_actually_studied_count_as_known() -> None:
    """A card sitting unstudied in the new queue is not knowledge. Colouring a word
    green because a card exists somewhere would describe the collection rather than
    the learner — on this collection that is 6,459 notes against 248."""
    seen: list[str] = []

    class Recording(FakeTransport):
        def __call__(self, req):
            if req.get("action") == "findNotes":
                seen.append(req["params"]["query"])
            return super().__call__(req)

    anki = AnkiConnectClient(transport=Recording({"findNotes": [], "notesInfo": []}))
    vocab.build_index("Korean", anki=anki)
    assert seen == ['deck:"Korean" -is:new']


def test_a_video_starting_re_reads_anki() -> None:
    """Reviews done since the last video have to show up the moment this one starts,
    and the index is otherwise trusted for five minutes."""
    builds = 0

    def counting(*a, **kw):
        nonlocal builds
        builds += 1
        return {"forms": {}, "lemmas": {}}

    real, vocab.build_index = vocab.build_index, counting
    try:
        vocab.invalidate_index()
        vocab.anki_index("Korean")
        vocab.anki_index("Korean")               # inside the TTL: cached
        assert builds == 1
        vocab.anki_index("Korean", refresh=True)  # a video starting: re-read
        assert builds == 2
    finally:
        vocab.build_index = real
        vocab.invalidate_index()


def test_anki_being_down_still_returns_words() -> None:
    """This runs while the learner is watching — a dead AnkiConnect must cost the
    colours, not the overlay."""
    class Dead:
        def invoke(self, *a, **kw):
            raise RuntimeError("connection refused")

    vocab.invalidate_index()
    lines = vocab.annotate(["물을 마셔요"], translate=False, anki=Dead())
    assert [t.surface for t in lines[0].tokens] == ["물", "마셔요"]
    assert all(t.status == "new" for t in lines[0].tokens)
    vocab.invalidate_index()


# --- glossing ------------------------------------------------------------

def test_the_model_is_asked_only_for_words_the_deck_cannot_answer() -> None:
    asked: list[str] = []

    class LLM:
        def complete_json(self, *, system, user, model, **kw):
            asked.append(user)
            return {"glosses": {"제국": "empire"}}

    anki = fake_anki([{"Front": "물", "Back": "water"}])
    vocab.invalidate_index()
    lines = vocab.annotate(["물과 제국"], provider=LLM(), anki=anki)
    assert len(asked) == 1
    items = [line.split("  (in:")[0] for line in asked[0].splitlines()]
    assert items == ["제국"]              # 물 is answered by the deck, for free
    assert lines[0].tokens[-1].gloss_en == "empire"
    vocab.invalidate_index()


def test_a_model_failure_is_not_fatal() -> None:
    class Broken:
        def complete_json(self, **kw):
            raise RuntimeError("no key")

    vocab.invalidate_index()
    lines = vocab.annotate(["제국"], provider=Broken(), anki=fake_anki([]))
    assert lines[0].tokens[0].surface == "제국"
    vocab.invalidate_index()


# --- the card a click makes ----------------------------------------------

def test_a_clicked_word_becomes_a_cloze_over_its_own_line() -> None:
    out = vocab.add_to_anki(word="먹었어요", context_ko="밥을 먹었어요", gloss_en="to eat",
                            pos="verb", display="먹어요", anki=_known("밥"),
                            tts_provider=_NoTTS())
    assert out["pushed"] == 1 and out["shape"] == "cloze"
    note = out["notes"][0]
    assert note["Chunk"] == "밥을 먹었어요"
    assert note["Prompt"] == "밥을 ______"      # the clicked word is the blank
    assert note["Gloss"] == "to eat"
    # the polite form goes in as the anchor, so the card shows 먹어요 -> 먹었어요
    assert note["Anchor"] == "먹어요" and note["Target"] == "먹었어요"


def test_a_clicked_noun_has_no_conjugation_derivation() -> None:
    out = vocab.add_to_anki(word="물을", context_ko="물을 마셔요", gloss_en="water",
                            pos="noun", display="물", anki=_known("물", "마셔요"),
                            tts_provider=_NoTTS())
    assert out["notes"][0]["Anchor"] == "" and out["notes"][0]["Target"] == ""


def test_the_back_bolds_the_word_that_was_hidden() -> None:
    out = vocab.add_to_anki(word="먹었어요", context_ko="밥을 먹었어요", gloss_en="to eat",
                            pos="verb", display="먹어요", anki=_known("밥", "먹었어요"),
                            tts_provider=_NoTTS())
    note = out["notes"][0]
    assert note["Answer"] == "밥을 <b>먹었어요</b>"
    assert note["Chunk"] == "밥을 먹었어요"        # stays clean for TTS and dedupe


# --- cloze only when the line can actually be read -----------------------

def test_a_second_unknown_word_in_the_clause_drops_the_sentence() -> None:
    """A cloze whose remaining words are also unknown is not a prompt — failing it
    cannot tell you which word you failed on."""
    out = vocab.add_to_anki(word="제국이", context_ko="제국이 전쟁을 일으켰고",
                            gloss_en="empire", pos="noun", display="제국", start=0,
                            anki=fake_anki([]), tts_provider=_NoTTS())
    assert out["shape"] == "isolated"
    assert sorted(out["blocked_by"]) == ["일으켰고", "전쟁"]
    note = out["notes"][0]
    assert note["Prompt"] == "제국" and note["Gloss"] == "empire"
    assert note["Chunk"] == "제국"              # the sentence is gone entirely


def test_an_isolated_verb_asks_for_its_polite_form() -> None:
    out = vocab.add_to_anki(word="일으켰고", context_ko="제국이 전쟁을 일으켰고",
                            gloss_en="to cause", pos="verb", display="일으켜요",
                            start=8, anki=fake_anki([]), tts_provider=_NoTTS())
    assert out["shape"] == "isolated"
    note = out["notes"][0]
    assert note["Prompt"] == "일으켰고"
    assert note["Anchor"] == "일으켰고" and note["Target"] == "일으켜요"


def test_the_two_shapes_go_to_different_decks() -> None:
    """Different reviews, so either can be tuned or suspended without the other —
    and both under Korean::, so a word mined today counts as known tomorrow."""
    cloze = vocab.add_to_anki(word="먹었어요", context_ko="밥을 먹었어요", pos="verb",
                              display="먹어요", start=3, anki=_known("밥"),
                              tts_provider=_NoTTS())
    word = vocab.add_to_anki(word="제국이", context_ko="제국이 전쟁을 일으켰고", start=0,
                             pos="noun", display="제국", anki=fake_anki([]),
                             tts_provider=_NoTTS())
    assert cloze["deck"] == config.ANKI_MINED_CLOZE_DECK
    assert word["deck"] == config.ANKI_MINED_WORD_DECK
    assert cloze["deck"] != word["deck"]
    assert all(d.startswith("Korean::") for d in (cloze["deck"], word["deck"]))


def test_a_readable_line_still_gets_the_cloze() -> None:
    out = vocab.add_to_anki(word="먹었어요", context_ko="밥을 먹었어요", gloss_en="to eat",
                            pos="verb", display="먹어요", start=3,
                            anki=_known("밥"), tts_provider=_NoTTS())
    assert out["shape"] == "cloze"
    assert out["notes"][0]["Prompt"] == "밥을 ______"


def test_only_the_clicked_word_s_own_clause_counts() -> None:
    """An opaque first clause does not make the second one unreadable — that is why
    the check is per clause and not per sentence."""
    line = "불의 제국이 전쟁을 일으켰고 밥을 먹었어요"
    out = vocab.add_to_anki(word="먹었어요", context_ko=line, gloss_en="to eat",
                            pos="verb", display="먹어요", start=line.index("먹었어요"),
                            anki=_known("밥"), tts_provider=_NoTTS())
    assert out["shape"] == "cloze" and out["blocked_by"] == []


class _NoTTS:
    def synth(self, *a, **kw):
        raise RuntimeError("no audio in tests")
