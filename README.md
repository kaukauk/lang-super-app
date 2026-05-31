# Korean Scaffolded-Immersion app

Learn Korean from ~10-minute cartoon segments: warm-up SRS reviews → AI extraction
(English primer + 15 lexical chunks) → blind watch → study re-watch with clickable
Korean subtitles → deck-constrained AI sparring → harvest tomorrow's chunks.

## Layout
- `backend/`  — FastAPI + SQLite service layer (Python 3.12+, in `.venv`). Also a full CLI.
- `frontend/` — React + Vite + Tailwind UI (built to `frontend/dist`, served by the backend).
- `youtube-to-transcript/` — original transcript fetcher (logic reused by the backend).
- `chatgpt-api-key.txt` — your OpenAI key (git-ignored). Or set `OPENAI_API_KEY`.

## Run (single process)
```bash
./run.sh            # builds the UI if needed, serves http://127.0.0.1:8765
```

## Develop (two processes, hot reload)
```bash
# terminal 1 — backend API
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8765
# terminal 2 — Vite dev server (proxies /api to :8765)
cd frontend && PATH="$HOME/.local/node/bin:$PATH" npm run dev    # http://localhost:5173
```

## CLI (headless — drives every feature)
```bash
cd backend
.venv/bin/python -m app.cli ingest-file ../youtube-to-transcript/AAWYtKPNhcA_ko.json
.venv/bin/python -m app.cli segments 1
.venv/bin/python -m app.cli extract 1 --accept          # add --fake for no API spend
.venv/bin/python -m app.cli due ; .venv/bin/python -m app.cli grade 1 good
.venv/bin/python -m app.cli export-txt deck.txt
.venv/bin/python -m app.cli spar-start --segment 1
.venv/bin/python -m app.cli spar 1 "안녕하세요"
```

## Test
```bash
cd backend && .venv/bin/python -m pytest          # full suite (offline, deterministic)
.venv/bin/python realapi_check.py                 # real-API smoke (extraction + sparring)
.venv/bin/python sim_week.py && cat sim_week_report.md   # full 6-day run-through
```

Models: extraction → `gpt-4o`, sparring → `gpt-4o` (override via `LANGAPP_*_MODEL`).
Anki interop: semicolon-separated TXT, content only (no scheduling crosses the boundary).
