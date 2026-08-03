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
# ~5-minute study days (4:45-5:15), snapped to caption gaps so a day never ends
# mid-sentence (the snap logic lives in segmenter.py).
SEG_MIN_S = int(os.environ.get("LANGAPP_SEG_MIN_S", "285"))      # 4:45
SEG_MAX_S = int(os.environ.get("LANGAPP_SEG_MAX_S", "315"))      # 5:15
SEG_TARGET_S = int(os.environ.get("LANGAPP_SEG_TARGET_S", "300"))  # 5:00
SEG_SNAP_WINDOW_S = int(os.environ.get("LANGAPP_SEG_SNAP_WINDOW_S", "30"))

# --- Extraction ----------------------------------------------------------
CHUNKS_PER_TYPE = 5        # 5 sentences + 5 noun_verb + 5 grammar = 15
CHUNK_TYPES = ("sentence", "noun_verb", "grammar")

# Number of AV target sentences turned into Anki cards per day.
AV_TARGETS_PER_SEGMENT = int(os.environ.get("LANGAPP_AV_TARGETS", "5"))

# Padding (seconds) around the cut media so clips don't cut off the last syllable.
# Whisper end timestamps tend to land slightly early, so the tail pad is larger.
AV_PAD_LEAD_S = float(os.environ.get("LANGAPP_AV_PAD_LEAD_S", "0.25"))
AV_PAD_TAIL_S = float(os.environ.get("LANGAPP_AV_PAD_TAIL_S", "0.6"))

# Verify-and-realign: re-transcribe a small window around each target and snap the
# cut to where the text actually is (turbo timestamps drift on noisy audio).
AV_VERIFY_ALIGN = os.environ.get("LANGAPP_AV_VERIFY_ALIGN", "1") not in ("0", "", "false")
AV_ALIGN_WINDOW_S = float(os.environ.get("LANGAPP_AV_ALIGN_WINDOW_S", "14.0"))
# After cutting, re-transcribe and DROP any card whose clip doesn't actually contain
# the target (better a few correct cards than one wrong one). Over-picks to backfill.
AV_VERIFY_CLIP = os.environ.get("LANGAPP_AV_VERIFY_CLIP", "1") not in ("0", "", "false")
# Context = the nearest preceding FULL sentence (skip fragments), within this many
# sentences back and this many seconds of lead-in (keeps the clip from getting long).
AV_CONTEXT_MAX_LOOKBACK = int(os.environ.get("LANGAPP_AV_CONTEXT_LOOKBACK", "4"))
AV_CONTEXT_MAX_LEAD_S = float(os.environ.get("LANGAPP_AV_CONTEXT_MAX_LEAD_S", "12.0"))

# --- Media (yt-dlp + ffmpeg) --------------------------------------------
MEDIA_CACHE_DIR = os.environ.get("LANGAPP_MEDIA_CACHE", str(REPO_ROOT / ".media_cache"))
YTDLP_BIN = os.environ.get("LANGAPP_YTDLP_BIN", "yt-dlp")
# Cap resolution — these clips are for shadowing/recall, not cinema; smaller = faster.
YTDLP_FORMAT = os.environ.get(
    "LANGAPP_YTDLP_FORMAT", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b")
FFMPEG_BIN = os.environ.get("LANGAPP_FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("LANGAPP_FFPROBE_BIN", "ffprobe")

# YouTube only serves playable formats to clients that can solve its JS "n"
# challenge; without one you get a bare "This video is not available". Solving it
# needs a JS runtime plus yt-dlp's EJS solver script. yt-dlp enables only deno by
# default, so autodetect (deno, then the node run.sh installs) unless overridden.
# Set either to "" to disable and go back to stock yt-dlp behaviour.
YTDLP_JS_RUNTIME = os.environ.get("LANGAPP_YTDLP_JS_RUNTIME", "auto")
YTDLP_REMOTE_COMPONENTS = os.environ.get("LANGAPP_YTDLP_REMOTE_COMPONENTS", "ejs:github")

# --- Whisper (faster-whisper, STT) --------------------------------------
# large-v3 (not turbo) — turbo trades timestamp accuracy, which matters for cutting
# clips at exact sentence boundaries on noisy/musical audio.
WHISPER_MODEL = os.environ.get("LANGAPP_WHISPER_MODEL", "Systran/faster-whisper-large-v3")
WHISPER_DEVICE = os.environ.get("LANGAPP_WHISPER_DEVICE", "auto")
WHISPER_COMPUTE = os.environ.get("LANGAPP_WHISPER_COMPUTE", "default")
WHISPER_LANG = os.environ.get("LANGAPP_WHISPER_LANG", "ko")
# VAD filtering removes music/silence so word timestamps don't drift on noisy audio
# (large-v3-turbo mistimes sentences badly without it). Strongly recommended on.
WHISPER_VAD = os.environ.get("LANGAPP_WHISPER_VAD", "1") not in ("0", "", "false")

# --- TTS (edge-tts, Primer audio only) ----------------------------------
TTS_VOICE = os.environ.get("LANGAPP_TTS_VOICE", "ko-KR-SunHiNeural")

# --- Anki (AnkiConnect) -------------------------------------------------
# NOTE: AnkiConnect listens on its default port 8765; to avoid a collision the
# app server was moved to 8770 (run.sh, vite proxy). Override with the env var if
# you reconfigure AnkiConnect's webBindPort.
ANKICONNECT_URL = os.environ.get("LANGAPP_ANKICONNECT_URL", "http://127.0.0.1:8765")
# A subdeck under your real Korean deck: lives in the Korean umbrella (syncs +
# reviews with it) but stays isolated so AV cards don't mix with other note types
# and can be tuned/suspended on their own. Override with LANGAPP_ANKI_DECK.
ANKI_DECK = os.environ.get("LANGAPP_ANKI_DECK", "Korean::Immersion")
ANKI_MODEL = os.environ.get("LANGAPP_ANKI_MODEL", "KoreanImmersion")
