"""Deterministic fake LLM provider for tests and offline CLI runs.

Supply either a `handler(system, user, model) -> dict` callable, or a list of
canned `responses` returned in order. Every call is recorded on `.calls` so
tests can assert on the prompts that were built.
"""

from __future__ import annotations

from typing import Any, Callable


class FakeLLM:
    def __init__(
        self,
        handler: Callable[..., dict[str, Any]] | None = None,
        responses: list[dict[str, Any]] | None = None,
    ) -> None:
        self.handler = handler
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "model": model})
        if self.handler is not None:
            return self.handler(system=system, user=user, model=model)
        if self.responses:
            return self.responses.pop(0)
        raise RuntimeError("FakeLLM ran out of canned responses")


class DemoLLM:
    """Offline provider that returns plausible, schema-valid Korean output for the
    CLI's --fake mode, so the whole pipeline runs with no API spend. It branches on
    the system prompt to serve extraction / sparring / harvest."""

    _SENT = ["안녕하세요", "잘 지냈어요", "오늘 날씨가 좋아요", "어디 가요", "뭐 먹을래요"]
    _NV = ["밥을 먹다", "물을 마시다", "책을 읽다", "사진을 찍다", "노래를 부르다"]
    _GRAM = ["하고 싶다", "갈 수 있다", "먹기 전에", "본 후에", "오는 중이다"]
    _HARVEST = ["비행기를 타다", "공원에서 놀다", "숙제를 하다", "영화를 보다"]

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        self.calls.append({"model": model})
        if "primer_md" in system:
            chunks = (
                [{"type": "sentence", "ko": k, "en": "(demo)", "gloss": "demo"} for k in self._SENT]
                + [{"type": "noun_verb", "ko": k, "en": "(demo)", "gloss": "demo"} for k in self._NV]
                + [{"type": "grammar", "ko": k, "en": "(demo)", "gloss": "demo"} for k in self._GRAM]
            )
            return {"primer_md": "Scene 1. A demo English-only primer.", "chunks": chunks}
        if "NEW useful" in system:
            return {"chunks": [
                {"type": "noun_verb", "ko": k, "en": "(demo)", "gloss": "demo"}
                for k in self._HARVEST
            ]}
        # sparring: reply with the first target so the validator passes
        import re
        m = re.search(r"TARGET items[^{]*\{([^}]*)\}", user)
        first = ""
        if m and m.group(1).strip() and m.group(1).strip() != "(none)":
            first = m.group(1).split(",")[0].strip()
        first = first or "안녕하세요"
        return {
            "reply_ko": f"{first}?",
            "glosses": {},
            "micro_feedback_en": "(demo) Good sentence.",
            "suggested_user_reply_ko": "네, 좋아요.",
            "suggested_user_reply_en": "Yes, sounds good.",
            "targets_used": [first],
        }
