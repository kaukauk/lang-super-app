"""FastAPI HTTP-layer smoke test — full daily loop offline via DemoLLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "youtube-to-transcript" / "AAWYtKPNhcA_ko.json"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LANGAPP_FAKE_LLM", "1")
    from app.main import app
    return TestClient(app)


def test_health(client) -> None:
    assert client.get("/api/health").json()["ok"] is True


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample transcript missing")
def test_full_flow_over_http(client) -> None:
    r = client.post("/api/videos/ingest", json={"path": str(SAMPLE)})
    assert r.status_code == 200
    vid = r.json()["video_id"]

    segs = client.get(f"/api/videos/{vid}/segments").json()
    # ~61-min sample at ~5-min days -> 12 study days + 1 full-review day.
    assert len(segs) == 13 and segs[-1]["is_full_review"] is True
    seg1 = segs[0]["id"]

    cues = client.get(f"/api/segments/{seg1}/cues").json()
    assert len(cues) > 10

    ex = client.post(f"/api/segments/{seg1}/extract", json={"accept": True, "today": 1000}).json()
    assert ex["counts"] == {"sentence": 5, "noun_verb": 5, "grammar": 5}
    assert ex["accepted"] == 15

    # Anki owns reviewing: accepted chunks are recorded as known vocab, never
    # scheduled, and the in-app review endpoints are gone with the warm-up page
    assert client.get("/api/reviews/due").status_code == 404
    # stats read Anki; with no collection reachable in tests it says so rather than
    # quietly reporting a number from a store that is no longer the master
    stats = client.get("/api/stats", params={"today": 1001}).json()
    assert stats["source"] == "unavailable" and stats["total"] is None

    # The TXT round-trip covers cards held in the app before they reach Anki, so it
    # needs its own rows now that accepting pushes straight to the collection.
    for ko, en in (("물", "water"), ("불", "fire")):
        client.post("/api/cards", json={"ko": ko, "en": en})
    txt = client.get("/api/export.txt").text
    assert txt.startswith("#separator:;")
    imp = client.post("/api/import", json={"content": txt, "deck": "Imported"}).json()
    assert imp["created"] == 2

    sess = client.post("/api/spar/start", json={"segment_id": seg1}).json()["session_id"]
    turn = client.post(f"/api/spar/{sess}/turn", json={"text": "안녕하세요"}).json()
    assert turn["reply_ko"]
    harv = client.post(f"/api/spar/{sess}/harvest", json={"accept": True, "today": 1001}).json()
    assert len(harv["chunks"]) >= 3
