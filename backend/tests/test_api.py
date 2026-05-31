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
    assert len(segs) == 7 and segs[-1]["is_full_review"] is True
    seg1 = segs[0]["id"]

    cues = client.get(f"/api/segments/{seg1}/cues").json()
    assert len(cues) > 10

    ex = client.post(f"/api/segments/{seg1}/extract", json={"accept": True, "today": 1000}).json()
    assert ex["counts"] == {"sentence": 5, "noun_verb": 5, "grammar": 5}
    assert len(ex["accepted_card_ids"]) == 15

    due = client.get("/api/reviews/due", params={"today": 1001}).json()
    assert len(due) == 15

    g = client.post(f"/api/reviews/{due[0]['id']}/grade", json={"grade": "good"}).json()
    assert g["state"] == "review"

    assert client.get("/api/stats", params={"today": 1001}).json()["total"] == 15

    txt = client.get("/api/export.txt").text
    assert txt.startswith("#separator:;")
    imp = client.post("/api/import", json={"content": txt, "deck": "Imported"}).json()
    assert imp["created"] == 15

    sess = client.post("/api/spar/start", json={"segment_id": seg1}).json()["session_id"]
    turn = client.post(f"/api/spar/{sess}/turn", json={"text": "안녕하세요"}).json()
    assert turn["reply_ko"]
    harv = client.post(f"/api/spar/{sess}/harvest", json={"accept": True, "today": 1001}).json()
    assert len(harv["chunks"]) >= 3
