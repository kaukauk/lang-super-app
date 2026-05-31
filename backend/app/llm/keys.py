"""Resolve the OpenAI API key. Mirrors the prior prototype's keys.py: env var
wins, otherwise read the git-ignored key file at the repo root."""

from __future__ import annotations

import os
from pathlib import Path

from .. import config


def resolve_openai_key(key_file: str | None = None) -> str | None:
    for env_key in ("OPENAI_API_KEY", "ANKI_OPENAI_API_KEY"):
        val = os.environ.get(env_key)
        if val and val.strip():
            return val.strip()
    path = Path(key_file or config.KEY_FILE)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
