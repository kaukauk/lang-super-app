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
./run.sh            # builds the UI if needed, serves http://127.0.0.1:8770
```

## Run over Tailscale (reachable from your other devices)
```bash
./run.sh --tailscale        # also publishes https://<this-machine>.<tailnet>.ts.net:8443
./run.sh --tailscale-off    # unpublish
```
`tailscale serve` proxies from the tailnet to the loopback listener, so the app is
reachable by devices signed into your tailnet and by nothing else — it is never bound
to the LAN. HTTPS uses a real Let's Encrypt cert, so no browser warnings.
Port 8443 is used because :443 on this node is taken; override with `TS_PORT=…`.

## Develop (two processes, hot reload)
```bash
# terminal 1 — backend API
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8770
# terminal 2 — Vite dev server (proxies /api to :8770)
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

## Anki add-on (for video autoplay)

AV cards embed the clip with `<video autoplay>`. Anki's webview is Chromium, which
refuses unmuted autoplay unless the document has a user activation — and Show
Answer is clicked in the reviewer's *bottom* webview, so the card's own document
never gets one and the clip sits paused. Install the add-on to clear that gate:

```bash
cp -r anki-addon/korean_immersion_autoplay ~/.local/share/Anki2/addons21/
```

Restart Anki. Without it the card still works, but you have to click or press a
key once per card to start the clip. Starting muted and unmuting is *not* a
workaround — Chromium pauses the element on unmute; see the add-on's docstring
for the measurements.

Clips are VP9/Opus WebM (`LANGAPP_AV_CLIP_EXT`) because Anki's bundled Qt
WebEngine ships without proprietary codecs: an H.264/AAC mp4 renders an empty
player.
