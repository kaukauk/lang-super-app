#!/usr/bin/env python3
"""Section a fetched transcript JSON into fixed-length time blocks.

Reads the JSON produced by main.py (a list of {text, start, duration}) and
groups the snippets into blocks (default: 10 minutes) by start time, writing
a readable markdown file with one heading per block.

Usage:
    python section.py <transcript.json> [--minutes 10] [--out blocks.md]
"""

import argparse
import json
import sys
from pathlib import Path


def fmt_ts(seconds: float) -> str:
    """Format seconds as H:MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Section a transcript into time blocks.")
    parser.add_argument("json_file", help="Transcript JSON produced by main.py")
    parser.add_argument(
        "--minutes", type=int, default=10, help="Block length in minutes (default: 10)"
    )
    parser.add_argument("--out", default=None, help="Output markdown file")
    args = parser.parse_args()

    snippets = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    block_secs = args.minutes * 60

    # Group snippets by which time block their start falls into.
    blocks: dict[int, list[dict]] = {}
    for snip in snippets:
        idx = int(snip["start"] // block_secs)
        blocks.setdefault(idx, []).append(snip)

    out_path = Path(args.out) if args.out else Path(args.json_file).with_name(
        Path(args.json_file).stem + f"_blocks_{args.minutes}min.md"
    )

    lines: list[str] = [f"# Transcript — {args.minutes}-minute blocks\n"]
    for idx in sorted(blocks):
        start = idx * block_secs
        end = start + block_secs
        lines.append(f"\n## {fmt_ts(start)} – {fmt_ts(end)}\n")
        # Join snippet texts into a flowing paragraph for the block.
        text = " ".join(s["text"].replace("\n", " ").strip() for s in blocks[idx])
        lines.append(text + "\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Sectioned {len(snippets)} snippets into {len(blocks)} blocks "
          f"of {args.minutes} min each.")
    for idx in sorted(blocks):
        start = idx * block_secs
        print(f"  {fmt_ts(start):>8} – {fmt_ts(start + block_secs):<8} "
              f"{len(blocks[idx]):>4} snippets")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
