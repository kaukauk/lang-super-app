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
    assert f["Derivation"] == "들어가요 → 들어가면 안 돼요"


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
    # no English-bearing field exists to reference; Ending/Derivation are Korean
    assert not {"English", "Gloss", "Meaning"} & set(payload["inOrderFields"])
    for side in (front, back):
        assert "{{English}}" not in side and "{{Gloss}}" not in side
    assert "{{Prompt}}" in front and "{{Audio}}" not in front   # audio would read the answer
    assert "{{Chunk}}" in back and "{{Audio}}" in back
    assert payload["inOrderFields"] == ["Prompt", "Chunk", "Ending", "Derivation", "Audio"]


def test_derivation_absent_without_an_anchor() -> None:
    """A guessed anchor would teach a form the learner never heard."""
    assert note("기름이 모자라요").fields()["Derivation"] == ""


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
