"""Analyzer (Kiwi) tests — real Korean morphology, no hand-maintained lists."""

from __future__ import annotations

from app.services import analyzer


def test_noun_particle_stripped_to_lemma() -> None:
    # 수영복을 -> 수영복 (particle 을 dropped, not a content lemma)
    lemmas = analyzer.content_lemmas("수영복을 갈아입다")
    assert "수영복" in lemmas
    assert "을" not in lemmas


def test_verb_dictionary_form() -> None:
    assert analyzer.content_lemmas("가다") == ["가다"]


def test_conjugations_collapse_to_same_lemma() -> None:
    base = analyzer.lemma_set("가다")
    assert analyzer.lemma_set("가요") == base
    assert analyzer.lemma_set("갔어요") == base
    assert "가다" in base


def test_irregular_adjective() -> None:
    # 추워요 (ㅂ-irregular) should lemmatize to 춥다
    assert "춥다" in analyzer.lemma_set("오늘 너무 추워요")


def test_particles_and_endings_are_not_content() -> None:
    lemmas = analyzer.content_lemmas("학교에 갔어요")
    assert "학교" in lemmas
    assert "가다" in lemmas
    assert "에" not in lemmas          # particle excluded


def test_content_lemmas_of_many() -> None:
    got = analyzer.content_lemmas_of_many(["학교", "갔어요", "수영복을"])
    assert {"학교", "가다", "수영복"} <= got
