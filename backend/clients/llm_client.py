"""
Shared LLM client — OpenAI.

All agents import get_llm_client() and get_llm_model() from here so the
API key and model are configured in one place.
"""

import os
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None

DEFAULT_MODEL = os.getenv("MODEL", "gpt-4o")
INTENT_MODEL  = os.getenv("INTENT_MODEL", "gpt-4o-mini")


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _client


def get_llm_model(intent: bool = False) -> str:
    return INTENT_MODEL if intent else DEFAULT_MODEL
