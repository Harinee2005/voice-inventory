"""
Shared LLM client — provider-switchable via env.

    LLM_PROVIDER=openai  (default) → api.openai.com, gpt-4o family
    LLM_PROVIDER=ollama            → local Ollama's OpenAI-compatible endpoint

All agents import get_llm_client() / get_llm_model() from here, so the
provider, API key, and models are configured in one place. Ollama model
defaults come from OLLAMA_* env vars — a stale MODEL=gpt-4o in .env can
never leak into an Ollama request.

Note: only the chat agents switch providers. Embeddings
(vector_memory_service) and speech (speech_service) stay on OpenAI —
pgvector tables are sized for text-embedding-3-small (1536 dims) and
Ollama has no Whisper/TTS equivalent; both degrade gracefully without quota.
"""

import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()  # idempotent — protects scripts that import us before database.py

_client: AsyncOpenAI | None = None

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

if LLM_PROVIDER == "ollama":
    _BASE_URL: str | None = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    _API_KEY = "ollama"  # SDK requires a value; Ollama ignores it
    _OLLAMA_DEFAULT = os.getenv("OLLAMA_MODEL", "qwen2.5")
    DEFAULT_MODEL = os.getenv("OLLAMA_MAIN_MODEL", _OLLAMA_DEFAULT)
    INTENT_MODEL = os.getenv("OLLAMA_INTENT_MODEL", _OLLAMA_DEFAULT)
    SCREEN_MODEL = os.getenv("OLLAMA_SCREEN_MODEL", _OLLAMA_DEFAULT)
else:
    _BASE_URL = None  # SDK default → api.openai.com
    _API_KEY = os.getenv("OPENAI_API_KEY", "")
    DEFAULT_MODEL = os.getenv("MODEL", "gpt-4o")
    INTENT_MODEL = os.getenv("INTENT_MODEL", "gpt-4o-mini")
    # Screening + extraction is a structured-parsing task — mini handles it well.
    # Rollback to gpt-4o is a single env var: SCREEN_MODEL=gpt-4o
    SCREEN_MODEL = os.getenv("SCREEN_MODEL", "gpt-4o-mini")


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=_API_KEY, base_url=_BASE_URL)
    return _client


def get_llm_model(intent: bool = False, screen: bool = False) -> str:
    if screen:
        return SCREEN_MODEL
    return INTENT_MODEL if intent else DEFAULT_MODEL
