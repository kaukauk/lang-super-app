"""Clean text-to-speech for the in-app PRIMER only (edge-tts, free/local).

This audio is for pre-watch shadowing and is NEVER pushed to Anki — the Anki
cards use the original audio cut from the video (see media.py). Output is cached
by (voice, text) hash so re-runs are instant."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .. import config


@runtime_checkable
class TTS(Protocol):
    def synth(self, text: str, out_path: str | Path, *, voice: Optional[str] = None) -> Path:
        ...


def _key(text: str, voice: str) -> str:
    return hashlib.sha1(f"{voice}\x1f{text}".encode("utf-8")).hexdigest()[:16]


class EdgeTTS:
    def synth(self, text: str, out_path: str | Path, *, voice: Optional[str] = None) -> Path:
        import edge_tts  # lazy: network-capable dep

        voice = voice or config.TTS_VOICE
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            await edge_tts.Communicate(text, voice).save(str(out))

        asyncio.run(_run())
        return out


class FakeTTS:
    """Writes a tiny valid (silent) MP3 frame so downstream code/tests have a real
    file without any network call."""

    # One silent MPEG-1 Layer III frame header + padding (decodes to ~26ms silence).
    _SILENT_MP3 = bytes.fromhex("fffb9064") + b"\x00" * 417

    def synth(self, text: str, out_path: str | Path, *, voice: Optional[str] = None) -> Path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self._SILENT_MP3)
        return out


def synth_cached(
    text: str, out_dir: str | Path, *, provider: Optional[TTS] = None,
    voice: Optional[str] = None, prefix: str = "primer",
) -> Path:
    """Synthesize `text` into `out_dir/<prefix>_<hash>.mp3`, reusing an existing
    file if present. Returns the path."""
    provider = provider or EdgeTTS()
    voice = voice or config.TTS_VOICE
    out = Path(out_dir) / f"{prefix}_{_key(text, voice)}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out
    return provider.synth(text, out, voice=voice)
