"""
Shared LLM client — Anthropic Claude only.

All agents import get_llm_client() / get_llm_model() from here, so the
API key and models are configured in one place.

    MODEL          — main conversational model (ARIA), default claude-haiku-4-5
    INTENT_MODEL    — cheap/fast intent classifier, default claude-haiku-4-5
    SCREEN_MODEL   — cheap/fast screen+extract, default claude-haiku-4-5

Every role defaults to claude-haiku-4-5 (lowest-cost Claude model) — set
MODEL to something higher-tier (e.g. claude-opus-4-8) if ARIA's response
quality needs to go up later.

Sampling params (temperature/top_p/top_k) are not exposed here — Claude
Opus 4.8 rejects non-default values, so every call site relies on the
model's default behaviour instead.

Structured output: prefer client.messages.parse() (grammar-constrained
JSON-schema decoding) for flat/simple schemas — see intent_agent.py. It
fails with a 400 "Schema is too complex" on schemas shaped like
ScreenedExtractionResult / ARIAResult (an array of objects each carrying
several large-cardinality enums) — same wall hits strict tool use
(strict=True). screen_extract_agent.py and aria_agent.py use
build_tool_schema() / extract_tool_input() below instead: a forced,
non-strict tool call, validated client-side via Pydantic afterward. No
complexity ceiling, same practical reliability.
"""

import json
import os
from typing import Any, Optional

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()  # idempotent — protects scripts that import us before database.py

_client: AsyncAnthropic | None = None

DEFAULT_MODEL = os.getenv("MODEL", "claude-haiku-4-5")
INTENT_MODEL  = os.getenv("INTENT_MODEL", "claude-haiku-4-5")
SCREEN_MODEL  = os.getenv("SCREEN_MODEL", "claude-haiku-4-5")


def get_llm_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    return _client


def get_llm_model(intent: bool = False, screen: bool = False) -> str:
    if screen:
        return SCREEN_MODEL
    return INTENT_MODEL if intent else DEFAULT_MODEL


def build_tool_schema(response_model: type) -> dict:
    """JSON schema for a Pydantic model, shaped for use as a tool's input_schema."""
    schema = response_model.model_json_schema()
    schema.pop("title", None)
    return schema


def extract_tool_input(response: Any) -> Optional[dict]:
    """Pull the input dict off the first tool_use block in a Claude response.

    Non-strict tool use occasionally serializes a nested array/object field
    as a JSON string instead of a native value (observed on ScreenedExtractionResult
    and ARIAResult's `items` field) — decode any top-level string field that
    looks like embedded JSON before returning, so Pydantic validation sees
    the native type it expects.
    """
    tool_input = None
    for block in response.content:
        if block.type == "tool_use":
            tool_input = block.input
            break
    if tool_input is None:
        return None
    fixed = dict(tool_input)
    for key, value in fixed.items():
        if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
            try:
                fixed[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass
    return fixed
