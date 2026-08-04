"""Test harness: every test gets a fresh, isolated SQLite database in a tmp dir."""

from __future__ import annotations

import pytest

from app import config, db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    db._engine = None          # force a new engine bound to the tmp DB
    db.init_db()
    yield
    db._engine = None


@pytest.fixture(autouse=True)
def no_live_anki(tmp_path, monkeypatch):
    """Keep the suite off the developer's real collection.

    Known vocabulary now comes from Anki, so an un-isolated test happily connects to
    a running AnkiConnect and pulls thousands of real forms into its prompts — which
    is how this was caught: a fixture's assertions started depending on whether Anki
    happened to be open. Point at an unroutable port so any live call fails fast,
    and give the cache its own directory so nothing leaks between runs.

    Tests that need known vocabulary inject a transport explicitly."""
    monkeypatch.setattr(config, "ANKICONNECT_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "MEDIA_CACHE_DIR", str(tmp_path / "cache"))
