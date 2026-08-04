"""The Korean-only harvested-chunk note type: blank selection and note payloads."""

from __future__ import annotations

from app.anki import chunks
from app.anki.connect import AnkiConnectClient, FakeTransport
from app.services import chunkcards
from app.services.extraction import Chunk


def note(ko, **kw) -> chunks.ChunkNote:
    return chunks.ChunkNote(chunk_ko=ko, **kw)


def test_correction_decides_the_blank() -> None:
    """The whole point of feeding corrections in: the blank lands on the element that
    actually failed, not on a guess."""
    f = note("밥을 먹었어요", corrected_from="밥 먹었어요").fields()
    assert f["Prompt"] == "밥__ 먹었어요"


def test_particle_blank_keeps_its_noun_visible() -> None:
    """Blanking the whole word would make any object answer the card — it would stop
    being about 밥 at all."""
    assert "밥" in note("밥을 먹었어요", corrected_from="밥 먹었어요").fields()["Prompt"]


def test_anchor_blanks_the_predicate_not_the_sentence() -> None:
    f = note("위험한 곳에 들어가면 안 돼요", anchor="들어가요", ending="-(으)면 안 돼요").fields()
    assert f["Prompt"] == "위험한 곳에 ______"
    # derivation is the transformation, not a restatement of the whole chunk
    assert f["Anchor"] == "들어가요" and f["Target"] == "들어가면 안 돼요"


def test_anchor_matches_across_a_different_conjugation() -> None:
    """생각해요 and 생각할 share only 생각 — containment would miss it."""
    assert chunks.anchor_word_index("생각할 수 있어요", "생각해요") == 0
    assert chunks.anchor_word_index("밥을 먹어요", "가요") == -1      # no coincidental hit


def test_blank_never_swallows_the_whole_chunk() -> None:
    for f in (note("기름이 모자라요").fields(),
              note("생각할 수 있어요", anchor="생각해요").fields()):
        assert chunks.BLANK in f["Prompt"]
        assert f["Prompt"] != chunks.BLANK          # something is always left to read


def test_single_word_chunk_is_not_blanked_into_nothing() -> None:
    f = note("모자라요").fields()
    assert f["Prompt"] == "모자라요"


def test_no_english_on_either_side() -> None:
    payload = chunks.build_model_payload("KoreanChunk")
    front, back = payload["cardTemplates"][0]["Front"], payload["cardTemplates"][0]["Back"]
    # Gloss exists but is back-only and populated only for lookup-derived cards,
    # where the learner explicitly asked for the English. It is never a prompt.
    assert "{{Gloss}}" not in front and "{{Gloss}}" in back
    assert "{{English}}" not in front and "{{English}}" not in back
    assert "{{Prompt}}" in front and "{{Audio}}" not in front   # audio would read the answer
    assert "{{Chunk}}" in back and "{{Audio}}" in back
    # the chip narrows the front to one correct answer, so it belongs there; on the
    # back the full chunk already resolves the blank and the chip only restates
    assert "{{Ending}}" in front and "{{Ending}}" not in back
    assert payload["inOrderFields"] == ["Prompt", "Chunk", "Context", "Ending",
                                       "Anchor", "Target", "Gloss", "Audio"]
    # the arrow is template markup, not data: every field stays clean enough to
    # generate audio from without a filter step
    assert "&rarr;" in back and all("→" not in v for v in payload["inOrderFields"])


def test_derivation_absent_without_an_anchor() -> None:
    """A guessed anchor would teach a form the learner never heard."""
    f = note("기름이 모자라요").fields()
    assert f["Anchor"] == "" and f["Target"] == ""


def test_push_creates_deck_and_model_then_adds_notes() -> None:
    ft = FakeTransport(results={"modelNames": []})
    out = chunkcards.push_chunks(
        [Chunk(type="grammar", ko="위험한 곳에 들어가면 안 돼요", en="",
               ending="-(으)면 안 돼요", anchor="들어가요")],
        anki=AnkiConnectClient(transport=ft), make_audio=False, session_id=7)
    actions = [r["action"] for r in ft.requests]
    assert "createDeck" in actions and "createModel" in actions and "addNote" in actions
    assert out["pushed"] == 1 and out["deck"] == "Korean::Chunks"
    added = next(r for r in ft.requests if r["action"] == "addNote")["params"]["note"]
    assert added["modelName"] == "KoreanChunk"
    assert "spar7" in added["tags"]


def test_push_skips_duplicates_instead_of_raising() -> None:
    def dup(req):
        if req["action"] == "addNote":
            return {"result": None, "error": "cannot create note because it is a duplicate"}
        return {"result": None, "error": None}
    out = chunkcards.push_chunks([Chunk(type="sentence", ko="밥을 먹어요", en="")],
                                 anki=AnkiConnectClient(transport=dup), make_audio=False)
    assert out["pushed"] == 0 and out["skipped"] == 1


def test_correction_is_matched_to_its_chunk() -> None:
    """The harvest often trims a correction to the reusable part or extends it, so
    matching has to work in both directions."""
    corrections = [{"original": "밥 먹었어요", "corrected": "밥을 먹었어요"}]
    notes = chunkcards.build_chunk_notes(
        [Chunk(type="sentence", ko="밥을 먹었어요", en=""),
         Chunk(type="sentence", ko="오늘 밥을 먹었어요", en=""),
         Chunk(type="sentence", ko="물을 마셔요", en="")],
        corrections=corrections)
    assert notes[0].corrected_from == "밥 먹었어요"
    assert notes[1].corrected_from == "밥 먹었어요"      # chunk extends the correction
    assert notes[2].corrected_from == ""                # unrelated chunk untouched


def test_audio_source_field_is_clean_korean() -> None:
    """The reason the derivation is split: a TTS add-on pointed at Chunk must never
    read notation or an arrow aloud."""
    f = note("위험한 곳에 들어가면 안 돼요", anchor="들어가요", ending="-(으)면 안 돼요").fields()
    assert f["Chunk"] == "위험한 곳에 들어가면 안 돼요"
    for junk in ("→", "-(", "(", "_", "[sound:"):
        assert junk not in f["Chunk"]
    # audio lives in its own field, so regenerating it never rewrites the sentence
    assert f["Audio"] == "" and "Audio" in f


def test_reconcile_adds_missing_fields_and_drops_only_empty_ones() -> None:
    """Removing a populated field destroys content Anki never gives back."""
    ft = FakeTransport(results={
        "modelNames": ["KoreanChunk"],
        "modelFieldNames": ["Prompt", "Chunk", "Ending", "Derivation", "Legacy"],
        "findNotes": [1],
        # Derivation is empty everywhere, Legacy is not
        "notesInfo": [{"fields": {"Derivation": {"value": ""},
                                  "Legacy": {"value": "keep me"}}}],
    })
    AnkiConnectClient(transport=ft).ensure_model(
        "KoreanChunk", payload=chunks.build_model_payload("KoreanChunk"))
    added = [r["params"]["fieldName"] for r in ft.requests if r["action"] == "modelFieldAdd"]
    removed = [r["params"]["fieldName"] for r in ft.requests if r["action"] == "modelFieldRemove"]
    assert set(added) == {"Context", "Anchor", "Target", "Gloss", "Audio"}
    assert removed == ["Derivation"]          # Legacy survives: it holds data


def test_lookup_card_blanks_the_clicked_word_in_its_question() -> None:
    """Your shape: the clicked word is the cloze, the AI's question is the chunk."""
    notes = chunkcards.notes_from_lookups([
        {"word": "하고", "context_ko": "오늘 뭐 하고 싶어요?",
         "gloss_en": "want to do", "ending": "-고 싶다"}])
    f = notes[0].fields()
    assert f["Prompt"] == "오늘 뭐 ______ 싶어요?"
    assert f["Chunk"] == "오늘 뭐 하고 싶어요?"
    assert f["Ending"] == "-고 싶다"


def test_clicked_fragment_widens_to_its_word() -> None:
    """Clicking 하고 means "I don't know this construction", not "hide two syllables"."""
    assert chunks.choose_blank("오늘 뭐 하고 싶어요?", blank_on="하고") == "하고"
    assert chunks.choose_blank("밥을 먹었어요", blank_on="밥") == "밥을"


def test_only_lookup_cards_carry_english() -> None:
    """A click is a comprehension gap — a cloze over a word learned seconds ago can be
    pattern-completed without the meaning sticking. Correction cards keep no English."""
    lookup = chunkcards.notes_from_lookups(
        [{"word": "하고", "context_ko": "오늘 뭐 하고 싶어요?", "gloss_en": "want to do"}])
    assert lookup[0].fields()["Gloss"] == "want to do"
    corrected = chunkcards.build_chunk_notes(
        [Chunk(type="sentence", ko="밥을 먹었어요", en="ate rice")],
        corrections=[{"original": "밥 먹었어요", "corrected": "밥을 먹었어요"}])
    assert corrected[0].fields()["Gloss"] == ""      # en is deliberately not carried


def test_context_is_the_line_the_chunk_answered() -> None:
    """Cues the card from meaning rather than a Korean frame you'll never have when
    speaking — the main weakness of a bare cloze."""
    history = [
        {"role": "assistant", "text": "어제 뭐 했어요?"},
        {"role": "user", "text": "저는 밥을 먹었어요"},
        {"role": "assistant", "text": "맛있었어요?"},
    ]
    notes = chunkcards.build_chunk_notes(
        [Chunk(type="sentence", ko="밥을 먹었어요", en="")], history=history)
    assert notes[0].fields()["Context"] == "어제 뭐 했어요?"


def test_lookup_without_a_usable_context_is_dropped() -> None:
    # a word with no sentence around it would blank into an empty card
    assert chunkcards.notes_from_lookups([{"word": "밥", "context_ko": ""}]) == []
    assert chunkcards.notes_from_lookups([{"word": "밥", "context_ko": "밥"}]) == []
