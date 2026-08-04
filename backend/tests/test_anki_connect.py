"""AnkiConnect payload construction + client behaviour against a FakeTransport."""

from __future__ import annotations

import pytest

from app.anki.connect import (
    AnkiConnectClient,
    AnkiError,
    FakeTransport,
    NoteSpec,
    build_model_payload,
    build_note_payload,
)


def test_note_fields_wrap_media_in_sound_tags() -> None:
    note = NoteSpec(target_ko="자전거를 타고 싶어요", target_audio_file="front.mp3",
                    context_ko="오늘 뭐 해요?", clip_file="clip.webm",
                    explanation_en="They want to ride a bike.")
    f = note.fields()
    assert f["TargetKo"] == "자전거를 타고 싶어요"
    assert f["TargetAudio"] == "[sound:front.mp3]"        # autoplays on front
    assert f["Clip"] == "clip.webm"                      # bare name; template embeds <video>
    assert f["ContextKo"] == "오늘 뭐 해요?"
    assert f["Explanation"] == "They want to ride a bike."


def test_model_payload_has_five_fields_and_one_template() -> None:
    p = build_model_payload("KoreanImmersion")
    assert p["inOrderFields"] == ["TargetKo", "TargetAudio", "ContextKo", "Clip", "Explanation"]
    assert len(p["cardTemplates"]) == 1
    front = p["cardTemplates"][0]["Front"]
    back = p["cardTemplates"][0]["Back"]
    assert "{{TargetKo}}" in front and "{{TargetAudio}}" in front
    assert "{{Clip}}" in back and "{{Explanation}}" in back
    assert "<video" in back and "autoplay" in back          # embedded, not an mpv handoff
    # back must NOT replay the front audio (would mask the video's own target audio)
    assert "{{FrontSide}}" not in back and "{{TargetAudio}}" not in back


def test_ensure_model_updates_existing_template() -> None:
    ft = FakeTransport(results={"modelNames": ["KoreanImmersion"]})
    AnkiConnectClient(transport=ft).ensure_model("KoreanImmersion")
    actions = [r["action"] for r in ft.requests]
    assert "updateModelTemplates" in actions and "updateModelStyling" in actions


def test_ensure_model_rewrites_a_differently_named_template() -> None:
    """A collection built by an earlier run carries its own template name; keying
    the update by ours would silently no-op and leave the stale template."""
    ft = FakeTransport(results={
        "modelNames": ["KoreanImmersion"],
        "modelTemplates": {"Immersion": {"Front": "old", "Back": "{{Clip}}"}},
    })
    AnkiConnectClient(transport=ft).ensure_model("KoreanImmersion")
    upd = next(r for r in ft.requests if r["action"] == "updateModelTemplates")
    templates = upd["params"]["model"]["templates"]
    assert list(templates) == ["Immersion"]
    assert "{{Explanation}}" in templates["Immersion"]["Back"]


def test_ensure_model_refuses_to_guess_among_several_templates() -> None:
    ft = FakeTransport(results={
        "modelNames": ["KoreanImmersion"],
        "modelTemplates": {"Card 1": {}, "Card 2": {}},
    })
    with pytest.raises(AnkiError):
        AnkiConnectClient(transport=ft).ensure_model("KoreanImmersion")


def test_note_payload_deck_model_and_dedupe() -> None:
    p = build_note_payload(NoteSpec(target_ko="가요", target_audio_file="a.mp3"),
                           deck="Korean", model="KoreanImmersion")
    assert p["deckName"] == "Korean" and p["modelName"] == "KoreanImmersion"
    assert p["options"]["duplicateScope"] == "deck"


def test_add_note_returns_id_and_sends_addnote() -> None:
    ft = FakeTransport()
    client = AnkiConnectClient(transport=ft)
    nid = client.add_note(NoteSpec(target_ko="가요", target_audio_file="a.mp3"))
    assert isinstance(nid, int)
    assert ft.requests[-1]["action"] == "addNote"
    assert ft.requests[-1]["version"] == 6


def test_ensure_model_creates_when_absent() -> None:
    ft = FakeTransport(results={"modelNames": []})
    AnkiConnectClient(transport=ft).ensure_model("KoreanImmersion")
    actions = [r["action"] for r in ft.requests]
    assert "createModel" in actions


def test_ensure_model_skips_when_present() -> None:
    ft = FakeTransport(results={"modelNames": ["KoreanImmersion"]})
    AnkiConnectClient(transport=ft).ensure_model("KoreanImmersion")
    assert "createModel" not in [r["action"] for r in ft.requests]


def test_store_media_resolves_path(tmp_path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    ft = FakeTransport()
    AnkiConnectClient(transport=ft).store_media_file("a.mp3", f)
    params = ft.requests[-1]["params"]
    assert params["filename"] == "a.mp3"
    assert params["path"] == str(f.resolve())


def test_add_note_skips_duplicate_instead_of_raising() -> None:
    def dup(req):
        if req["action"] == "addNote":
            return {"result": None, "error": "cannot create note because it is a duplicate"}
        return {"result": None, "error": None}
    nid = AnkiConnectClient(transport=dup).add_note(
        NoteSpec(target_ko="가요", target_audio_file="a.mp3"))
    assert nid is None          # skipped, not an exception


def test_delete_notes_by_tag() -> None:
    ft = FakeTransport(results={"findNotes": [11, 22, 33]})
    n = AnkiConnectClient(transport=ft).delete_notes_by_tag("seg9")
    assert n == 3
    actions = [r["action"] for r in ft.requests]
    assert "findNotes" in actions and "deleteNotes" in actions


def test_error_response_raises() -> None:
    def boom(_req):
        return {"result": None, "error": "deck not found"}
    client = AnkiConnectClient(transport=boom)
    try:
        client.invoke("addNote")
        assert False, "expected AnkiError"
    except Exception as exc:
        assert "deck not found" in str(exc)
