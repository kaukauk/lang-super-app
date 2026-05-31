"""OpenAI JSON client.

Transport approach reused from the prior Anki prototype's openai.py (retry +
backoff, JSON-object responses) but decoupled from Anki: it uses a plain
requests.Session and exposes a single `complete_json` method. Both the real
client and the FakeLLM satisfy the `LLMProvider` protocol so services never
care which one they got.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any, Protocol, runtime_checkable

import requests

from .keys import resolve_openai_key

_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Any failure talking to the model or parsing its JSON."""


@runtime_checkable
class LLMProvider(Protocol):
    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        ...


class OpenAIClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: tuple[float, float] = (5.0, 90.0),
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 8.0,
    ) -> None:
        self.api_key = api_key or resolve_openai_key()
        if not self.api_key:
            raise LLMError(
                "No OpenAI API key found. Set OPENAI_API_KEY or create "
                "chatgpt-api-key.txt at the repo root."
            )
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self._session = requests.Session()

    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = self._post(payload, headers)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response shape: {data!r}") from exc
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model did not return valid JSON: {content[:300]!r}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("Model returned non-object JSON")
        return parsed

    def _post(self, payload: dict, headers: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            resp = None
            try:
                resp = self._session.post(
                    _CHAT_URL, data=json.dumps(payload), headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code in _RETRY_STATUS and attempt < self.max_retries:
                    self._sleep(attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleep(attempt)
                    continue
                raise LLMError(f"OpenAI request failed: {exc}") from exc
            except requests.HTTPError as exc:
                code = resp.status_code if resp is not None else "?"
                body = resp.text[:300] if resp is not None else ""
                raise LLMError(f"OpenAI HTTP {code}: {body}") from exc
            finally:
                if resp is not None:
                    resp.close()
        raise LLMError(f"OpenAI request failed after retries: {last_exc}")

    def _sleep(self, attempt: int) -> None:
        backoff = min(self.backoff_max_s, self.backoff_base_s * (2 ** attempt))
        time.sleep(random.random() * backoff)
