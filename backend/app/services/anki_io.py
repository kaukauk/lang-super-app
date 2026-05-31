"""Anki-compatible TXT import/export.

Format: semicolon-separated, three columns `Korean;English;Gloss`, with Anki's
`#separator`/`#columns` header lines. CONTENT ONLY — no SRS/scheduling/deck-state
ever crosses this boundary (per product decision). Imported cards enter as fresh
'new' cards; by default they are left unscheduled (known vocab for the LLM but
not flooding the review queue) unless schedule_now=True.

Quoting/escaping uses the stdlib csv module so fields containing ';', '"' or
newlines round-trip correctly and remain importable by Anki.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from . import srs

HEADER_LINES = ["#separator:;", "#html:false", "#columns:Korean;English;Gloss"]
_COLUMNS_SENTINEL = ["korean", "english", "gloss"]


def export_txt_string(*, deck_name: str = "Korean") -> str:
    cards = srs.list_cards(deck_name=deck_name)
    buf = io.StringIO()
    for line in HEADER_LINES:
        buf.write(line + "\n")
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    for c in cards:
        writer.writerow([c.front_ko, c.back_en, c.gloss])
    return buf.getvalue()


def export_txt(path: str | Path, *, deck_name: str = "Korean") -> int:
    text = export_txt_string(deck_name=deck_name)
    Path(path).write_text(text, encoding="utf-8")
    return len(srs.list_cards(deck_name=deck_name))


def import_txt_string(text: str, *, deck_name: str = "Korean", schedule_now: bool = False) -> dict[str, Any]:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("#"):
        i += 1
    return _import_rows("\n".join(lines[i:]), deck_name=deck_name, schedule_now=schedule_now)


def import_txt(
    path: str | Path, *, deck_name: str = "Korean", schedule_now: bool = False,
) -> dict[str, Any]:
    return import_txt_string(Path(path).read_text(encoding="utf-8"),
                             deck_name=deck_name, schedule_now=schedule_now)


def _import_rows(data: str, *, deck_name: str, schedule_now: bool) -> dict[str, Any]:
    today = srs.today_ordinal()
    created = 0
    skipped = 0
    for row in csv.reader(io.StringIO(data), delimiter=";"):
        if not row or all(not cell.strip() for cell in row):
            continue
        ko = row[0].strip() if len(row) > 0 else ""
        en = row[1].strip() if len(row) > 1 else ""
        gloss = row[2].strip() if len(row) > 2 else ""
        if not ko:
            skipped += 1
            continue
        # tolerate a plain (un-#'d) header row of column names
        if [ko.lower(), en.lower(), gloss.lower()] == _COLUMNS_SENTINEL:
            continue
        srs.add_card(
            front_ko=ko, back_en=en, gloss=gloss, kind="imported",
            surface_forms=[ko], deck_name=deck_name, state="new",
            due_ord=(today if schedule_now else None),
        )
        created += 1
    return {"created": created, "skipped": skipped}
