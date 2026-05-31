"""Standalone REAL-API check (no --fake): extraction + sparring against OpenAI.
Writes a human-readable report to realapi_result.txt."""
import os
import re

os.environ["LANGAPP_DB"] = "/tmp/realapi_check.db"
try:
    os.remove("/tmp/realapi_check.db")
except OSError:
    pass

from app import db
from app.llm import OpenAIClient
from app.llm.keys import resolve_openai_key
from app.services import extraction, ingest, sparring, srs

lines = []


def log(s=""):
    lines.append(str(s))


key = resolve_openai_key()
log(f"key resolved from chatgpt-api-key.txt: {bool(key)}  sk-prefix: {bool(key and key.startswith('sk-'))}")

db.init_db()
summary = ingest.ingest_from_file("../youtube-to-transcript/AAWYtKPNhcA_ko.json")
log(f"ingested {summary['n_cues']} cues, {summary['n_study_segments']} study segments")
seg1 = [s for s in ingest.get_segments(summary["video_id"]) if not s.is_full_review][0]

client = OpenAIClient()  # REAL OpenAI client

# --- real extraction ---
log("\n=== REAL extraction (gpt-4o-mini) ===")
res = extraction.extract_segment(seg1.id, client, model="gpt-4o-mini")
log(f"counts: {res.counts()}  warnings: {res.warnings}")
log(f"primer English-only: {not bool(re.search(r'[가-힣]', res.primer_md))}")
log("primer head: " + res.primer_md[:160].replace(chr(10), " "))
for c in res.chunks[:4]:
    log(f"  [{c.type}] {c.ko} — {c.en}")
extraction.accept_chunks(res.chunks, segment_id=seg1.id, today_ord=1000)

# --- real sparring ---
log("\n=== REAL sparring turn (gpt-4o-mini) ===")
targets = [c.front_ko for c in srs.list_cards() if c.state == "new"][:15]
support = sorted(srs.known_surface_forms() - set(targets))
log(f"targets (today's new cards): {targets[:5]} ... ({len(targets)} total)")
resp = sparring.spar_turn(
    user_text="안녕하세요. 수영 좋아해요.", provider=client,
    targets=targets, support=support, scene=res.primer_md[:300], model="gpt-4o-mini",
)
log(f"reply_ko:        {resp.reply_ko}")
log(f"targets_used:    {resp.targets_used}")
log(f"unexpected:      {resp.unexpected}   <- Kiwi validator flags out-of-deck words")
log(f"micro_feedback:  {resp.micro_feedback_en[:120]}")
log(f"suggested_ko:    {resp.suggested_user_reply_ko}")
log(f"warnings:        {resp.warnings}")

log("\nALL REAL-API CALLS SUCCEEDED")
open("realapi_result.txt", "w", encoding="utf-8").write("\n".join(lines))
print("done")
