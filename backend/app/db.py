"""SQLite engine + session helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from . import config

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{config.DB_PATH}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    """Create all tables. Importing models registers them on SQLModel.metadata."""
    from . import models  # noqa: F401  (side-effect: table registration)

    SQLModel.metadata.create_all(get_engine())
    _ensure_columns()


# Additive columns introduced after a table first shipped. create_all() never
# ALTERs an existing table, so we add any missing ones here (SQLite-safe, no-op
# on a fresh DB). Keep this list tiny — full migrations are out of scope.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "sparsession": [("role_en", "TEXT DEFAULT ''"), ("scene_en", "TEXT DEFAULT ''")],
}


def _ensure_columns() -> None:
    from sqlalchemy import text

    with get_engine().begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if not existing:
                continue  # table doesn't exist yet (create_all handles new DBs)
            for name, decl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {decl}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error.

    expire_on_commit=False keeps attribute values readable on objects after the
    session closes, so the service layer can return ORM rows safely.
    """
    s = Session(get_engine(), expire_on_commit=False)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Session:
    return Session(get_engine(), expire_on_commit=False)
