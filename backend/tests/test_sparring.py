"""Sparring tests — analyzer-backed vocab validation + the rewrite loop, offline."""

from __future__ import annotations

from app.llm import FakeLLM
from app.services import sparring as sp


def spar(reply, *, tu=None, glosses=None):
    return {
        "reply_ko": reply,
        "glosses": glosses or {},
        "micro_feedback_en": "Good sentence.",
        "suggested_user_reply_ko": "네, 좋아요.",
        "suggested_user_reply_en": "Yes, sounds good.",
        "targets_used": tu or [],
    }


# --- analyzer-backed validator ---

def test_analyze_reply_detects_target_via_lemma() -> None:
    used, unexpected = sp.analyze_reply("오늘 학교에 가요?", {"가다", "학교", "오늘"}, ["가다"])
    assert used == ["가다"]               # 가요 -> 가다 matched by lemma
    assert unexpected == []


def test_analyze_reply_flags_out_of_vocab() -> None:
    used, unexpected = sp.analyze_reply("수영복을 입어요?", {"가다", "학교"}, ["가다"])
    assert used == []
    assert "수영복" in unexpected           # content word outside allowed set


# --- spar_turn rewrite loop ---

def test_spar_turn_happy_single_call() -> None:
    llm = FakeLLM(responses=[spar("오늘 학교에 가요?", tu=["가다"])])
    r = sp.spar_turn(user_text="안녕하세요", provider=llm,
                     targets=["가다"], support=["학교", "오늘"])
    assert r.targets_used == ["가다"]
    assert r.unexpected == []
    assert len(llm.calls) == 1


def test_spar_turn_rewrites_until_valid() -> None:
    llm = FakeLLM(responses=[spar("수영복을 입어요?"), spar("학교에 가요?", tu=["가다"])])
    r = sp.spar_turn(user_text="네", provider=llm, targets=["가다"], support=["학교"])
    assert r.targets_used == ["가다"]
    assert r.unexpected == []
    assert len(llm.calls) == 2
    assert "TARGET" in llm.calls[1]["user"]   # corrective instruction sent


def test_spar_turn_advisory_after_max_rewrites() -> None:
    llm = FakeLLM(responses=[spar("밥을 먹어요?"), spar("물을 마셔요?")])
    r = sp.spar_turn(user_text="네", provider=llm, targets=["가다"],
                     support=["밥", "물"], max_rewrites=1)
    assert r.targets_used == []
    assert any("no target" in w for w in r.warnings)
    assert len(llm.calls) == 2


def test_spar_turn_invalid_json_then_valid() -> None:
    llm = FakeLLM(responses=[{"reply_ko": ""}, spar("학교에 가요?", tu=["가다"])])
    r = sp.spar_turn(user_text="네", provider=llm, targets=["가다"], support=["학교"])
    assert r.reply_ko == "학교에 가요?"
    assert len(llm.calls) == 2


# --- harvest ---

def test_harvest_dedupes_known() -> None:
    chunks = [
        {"type": "sentence", "ko": "학교", "en": "school", "gloss": "x"},   # known -> dropped
        {"type": "sentence", "ko": "비행기를 타다", "en": "ride a plane", "gloss": "x"},
        {"type": "noun_verb", "ko": "사진을 찍다", "en": "take a photo", "gloss": "x"},
        {"type": "grammar", "ko": "먹고 싶다", "en": "want to eat", "gloss": "x"},
        {"type": "sentence", "ko": "노래를 부르다", "en": "sing a song", "gloss": "x"},
    ]
    llm = FakeLLM(responses=[{"chunks": chunks}])
    out = sp.harvest_chunks(history=[{"role": "user", "text": "안녕"}],
                            provider=llm, known={"학교"})
    kos = {c.ko for c in out}
    assert "학교" not in kos
    assert 3 <= len(out) <= 5


# --- persistence ---

def test_session_persistence() -> None:
    sess = sp.start_session(segment_id=None)
    sp.record_turn(sess.id, "user", text_ko="안녕하세요")
    sp.record_turn(sess.id, "assistant", text_ko="안녕하세요! 오늘 뭐 해요?",
                   targets_used=["하다"])
    hist = sp.session_history(sess.id)
    assert [t["role"] for t in hist] == ["user", "assistant"]
    assert hist[1]["text"].startswith("안녕")


def test_analyze_reply_returns_full_target_surface_not_lemma() -> None:
    targets = ["안녕하세요", "어디 가요"]
    used, unexpected = sp.analyze_reply("안녕하세요?", set(targets), targets)
    assert used == ["안녕하세요"]      # the target STRING, not its lemma 안녕
    assert unexpected == []


# --- n+1 policy: tolerate exactly one new content word ---

def test_spar_turn_allows_one_new_word() -> None:
    # reply uses the target (가다) plus exactly ONE unknown content word (수영) ->
    # n+1 stretch, accepted on the first call (no rewrite).
    llm = FakeLLM(responses=[spar("수영 가요?", tu=["가다"])])
    r = sp.spar_turn(user_text="네", provider=llm, targets=["가다"], support=["학교"])
    assert r.targets_used == ["가다"]
    assert len(r.unexpected) == 1 and "수영" in r.unexpected
    assert len(llm.calls) == 1
    assert any("n+1" in w for w in r.warnings)


def test_spar_turn_rewrites_on_two_new_words() -> None:
    # two unknown content words -> over budget -> rewrite to a clean reply.
    llm = FakeLLM(responses=[spar("수영복을 입어요?"), spar("학교에 가요?", tu=["가다"])])
    r = sp.spar_turn(user_text="네", provider=llm, targets=["가다"], support=["학교"])
    assert r.targets_used == ["가다"] and r.unexpected == []
    assert len(llm.calls) == 2
    assert "AT MOST" in llm.calls[1]["user"]


# --- opener: Anchor + Prompt state machine ---

def _opening(anchor="안녕하세요. 또 봐요.", role="the customer"):
    return {"anchor_ko": anchor, "role_en": role,
            "prompt_en": "You are the customer. What do you say?",
            "scene_en": "A cafe exchange.", "glosses": {"안녕하세요": "hello"}}


def test_spar_open_persists_role_and_records_turn() -> None:
    sess = sp.start_session(segment_id=None)
    llm = FakeLLM(responses=[_opening(role="the shopkeeper")])
    opening = sp.spar_open(sess.id, provider=llm, segment_id=None)
    assert opening.role_en == "the shopkeeper"
    assert opening.prompt_en
    # role persisted on the session
    assert sp.get_session(sess.id).role_en == "the shopkeeper"
    # opening recorded as the first assistant turn
    hist = sp.session_history(sess.id)
    assert hist and hist[0]["role"] == "assistant"
    assert hist[0]["text"] == opening.anchor_ko


def test_spar_open_falls_back_to_real_lines_when_llm_blank() -> None:
    sess = sp.start_session(segment_id=None)
    llm = FakeLLM(responses=[_opening(anchor="")])
    # no segment -> no closing lines -> anchor stays empty but role still set
    opening = sp.spar_open(sess.id, provider=llm, segment_id=None)
    assert opening.role_en == "the customer"
