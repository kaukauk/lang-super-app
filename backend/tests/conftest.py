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
