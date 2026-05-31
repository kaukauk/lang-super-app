"""LLM layer: a thin OpenAI JSON client (transport reused from the prior Anki
prototype's approach) plus a fake provider for deterministic testing."""

from .client import LLMError, LLMProvider, OpenAIClient
from .fake import FakeLLM

__all__ = ["LLMError", "LLMProvider", "OpenAIClient", "FakeLLM"]
