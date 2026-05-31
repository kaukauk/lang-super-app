"""Central configuration. Everything is overridable via environment variables so
the CLI and the test-suite can point at throwaway databases and fake keys."""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/config.py -> backend/ -> repo root
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# --- Storage -------------------------------------------------------------
DB_PATH = os.environ.get("LANGAPP_DB", str(BACKEND_DIR / "app.db"))

# --- OpenAI --------------------------------------------------------------
# The key file lives at the repo root and is git-ignored. Env var wins.
KEY_FILE = os.environ.get("LANGAPP_OPENAI_KEY_FILE", str(REPO_ROOT / "chatgpt-api-key.txt"))
EXTRACTION_MODEL = os.environ.get("LANGAPP_EXTRACTION_MODEL", "gpt-4o")
SPARRING_MODEL = os.environ.get("LANGAPP_SPARRING_MODEL", "gpt-4o")

# --- Segmentation (seconds) ---------------------------------------------
SEG_MIN_S = int(os.environ.get("LANGAPP_SEG_MIN_S", "540"))      # 9 min
SEG_MAX_S = int(os.environ.get("LANGAPP_SEG_MAX_S", "660"))      # 11 min
SEG_TARGET_S = int(os.environ.get("LANGAPP_SEG_TARGET_S", "600"))  # 10 min
SEG_SNAP_WINDOW_S = int(os.environ.get("LANGAPP_SEG_SNAP_WINDOW_S", "45"))

# --- Extraction ----------------------------------------------------------
CHUNKS_PER_TYPE = 5        # 5 sentences + 5 noun_verb + 5 grammar = 15
CHUNK_TYPES = ("sentence", "noun_verb", "grammar")
