#!/usr/bin/env python3
"""Fetch a YouTube video's transcript/subtitles and save it in several formats.

Usage:
    python main.py <video_id_or_url> [--languages ko en] [--translate en]

Examples:
    python main.py AAWYtKPNhcA
    python main.py "https://youtu.be/AAWYtKPNhcA" --languages ko
    python main.py AAWYtKPNhcA --languages ko --translate en
"""

import argparse
import re
import sys
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import (
    JSONFormatter,
    SRTFormatter,
    TextFormatter,
)


def extract_video_id(value: str) -> str:
    """Accept a raw video id or any common YouTube URL and return the id."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value

    m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", value)
    if m:
        return m.group(1)

    raise ValueError(f"Could not extract a video id from: {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript.")
    parser.add_argument("video", help="YouTube video id or URL")
    parser.add_argument(
        "--languages",
        nargs="+",
        default=["en"],
        help="Preferred language codes in priority order (default: en)",
    )
    parser.add_argument(
        "--translate",
        default=None,
        help="Translate the fetched transcript to this language code",
    )
    parser.add_argument(
        "--outdir",
        default=".",
        help="Directory to write output files (default: current dir)",
    )
    args = parser.parse_args()

    video_id = extract_video_id(args.video)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    ytt_api = YouTubeTranscriptApi()

    # Show what transcripts exist so the user knows what's available.
    print(f"Listing available transcripts for video: {video_id}")
    transcript_list = ytt_api.list(video_id)
    for t in transcript_list:
        kind = "generated" if t.is_generated else "manual"
        translatable = "translatable" if t.is_translatable else "not translatable"
        print(f"  - {t.language_code:8} {t.language!r:30} [{kind}, {translatable}]")
    print()

    # Pick the transcript matching the requested languages.
    transcript = transcript_list.find_transcript(args.languages)
    print(
        f"Selected: {transcript.language} ({transcript.language_code}), "
        f"{'generated' if transcript.is_generated else 'manual'}"
    )

    if args.translate:
        print(f"Translating to '{args.translate}'...")
        transcript = transcript.translate(args.translate)

    fetched = transcript.fetch()
    print(f"Fetched {len(fetched)} snippets.\n")

    # Preview the first few lines.
    print("--- Preview ---")
    for snippet in fetched[:5]:
        print(f"[{snippet.start:7.2f}s] {snippet.text}")
    print("...\n")

    # Write out text, SRT, and JSON formats.
    lang = transcript.language_code
    base = outdir / f"{video_id}_{lang}"

    writers = {
        f"{base}.txt": TextFormatter(),
        f"{base}.srt": SRTFormatter(),
        f"{base}.json": JSONFormatter(),
    }
    for path, formatter in writers.items():
        kwargs = {"indent": 2} if isinstance(formatter, JSONFormatter) else {}
        content = formatter.format_transcript(fetched, **kwargs)
        Path(path).write_text(content, encoding="utf-8")
        print(f"Wrote {path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the CLI
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
