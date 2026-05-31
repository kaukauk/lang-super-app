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
