"""
Timeout + retry wrapper for Claude API calls.

All agents (aria, intent, screen_extract) use `call_llm()` instead of
calling `client.messages.create()` / `client.messages.parse()` directly.
This gives us:
  - per-attempt timeout via asyncio.wait_for
  - exponential backoff on 429 / transient network errors
  - circuit breaker integration (fails fast when API is repeatedly down)
  - unified token-cost logging (input + output tokens, estimated USD)

Usage:
    response = await call_llm(
        lambda: client.messages.parse(...),
        timeout=25.0,
        label="aria",
    )
"""

import asyncio
import logging
import os
import time
from typing import Any, Callable, Coroutine

from utils.circuit_breaker import get_breaker, ServiceUnavailableError

logger = logging.getLogger(__name__)

# Default per-attempt timeout by agent label
_DEFAULT_TIMEOUTS: dict[str, float] = {
    "aria":           float(os.getenv("ARIA_TIMEOUT",           "25")),
    "intent":         float(os.getenv("INTENT_TIMEOUT",         "10")),
    "screen_extract": float(os.getenv("SCREEN_EXTRACT_TIMEOUT", "15")),
}
_FALLBACK_TIMEOUT = 25.0
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
_BACKOFF_BASE = 1.5  # seconds: 1.5s, 2.25s, 3.375s

# Token cost rates (per 1 000 tokens, USD)
_OPUS_PROMPT_RATE      = float(os.getenv("CLAUDE_OPUS_PROMPT_RATE",      "0.005"))   # claude-opus-4-8 input
_OPUS_COMPLETION_RATE  = float(os.getenv("CLAUDE_OPUS_COMPLETION_RATE",  "0.025"))   # claude-opus-4-8 output
_HAIKU_PROMPT_RATE     = float(os.getenv("CLAUDE_HAIKU_PROMPT_RATE",     "0.001"))   # claude-haiku-4-5 input
_HAIKU_COMPLETION_RATE = float(os.getenv("CLAUDE_HAIKU_COMPLETION_RATE", "0.005"))   # claude-haiku-4-5 output


def _is_retryable(exc: Exception) -> bool:
    """True for transient errors that are worth retrying."""
    name = type(exc).__name__
    msg  = str(exc).lower()
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                "InternalServerError", "ServiceUnavailableError"):
        return True
    if "rate limit" in msg or "timeout" in msg or "connection" in msg or "502" in msg or "503" in msg:
        return True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    return False


def _log_tokens(response: Any, label: str, model: str | None = None) -> None:
    """Log token usage and estimated cost from a Claude response."""
    usage = getattr(response, "usage", None)
    if not usage:
        return
    prompt_tok     = getattr(usage, "input_tokens",  0)
    completion_tok = getattr(usage, "output_tokens", 0)
    is_haiku = "haiku" in (model or "").lower()
    p_rate = _HAIKU_PROMPT_RATE     if is_haiku else _OPUS_PROMPT_RATE
    c_rate = _HAIKU_COMPLETION_RATE if is_haiku else _OPUS_COMPLETION_RATE
    cost   = (prompt_tok * p_rate + completion_tok * c_rate) / 1_000
    logger.info(
        "TOKENS  agent=%s  model=%s  prompt=%d  completion=%d  cost=$%.5f",
        label, model or "?", prompt_tok, completion_tok, cost,
    )


async def call_llm(
    coro_fn: Callable[[], Coroutine[Any, Any, Any]],
    *,
    timeout: float | None = None,
    label: str = "llm",
    model: str | None = None,
) -> Any:
    """
    Call a Claude coroutine with timeout, exponential-backoff retry, and circuit breaker.

    Args:
        coro_fn : zero-argument callable returning the coroutine (called fresh on each attempt)
        timeout : per-attempt timeout in seconds (defaults by label from env)
        label   : agent name used in logs and timeout lookup
        model   : model id for cost calculation (optional; read from response if omitted)

    Returns:
        The successful Claude response object.

    Raises:
        ServiceUnavailableError : circuit breaker is open
        asyncio.TimeoutError    : all retries timed out
        Exception               : last non-retryable error
    """
    t_out = timeout or _DEFAULT_TIMEOUTS.get(label, _FALLBACK_TIMEOUT)
    breaker = get_breaker()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        t0 = time.perf_counter()
        try:
            async with breaker:
                response = await asyncio.wait_for(coro_fn(), timeout=t_out)
            elapsed = (time.perf_counter() - t0) * 1000
            # Infer model from response if not provided
            resp_model = model or getattr(response, "model", None)
            _log_tokens(response, label, resp_model)
            if attempt > 0:
                logger.info("LLM_RETRY  agent=%s  succeeded on attempt %d  elapsed=%.0fms", label, attempt + 1, elapsed)
            return response

        except ServiceUnavailableError:
            raise  # circuit breaker open — don't retry

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            if not _is_retryable(exc) or attempt == _MAX_RETRIES - 1:
                logger.error(
                    "LLM_CALL FAILED  agent=%s  attempt=%d/%d  error_type=%s  "
                    "error=%s  elapsed=%.0fms",
                    label, attempt + 1, _MAX_RETRIES, type(exc).__name__, exc, elapsed,
                )
                raise
            delay = _BACKOFF_BASE ** attempt
            logger.warning(
                "LLM_RETRY  agent=%s  attempt=%d/%d  error=%s  retrying in %.1fs",
                label, attempt + 1, _MAX_RETRIES, type(exc).__name__, delay,
            )
            last_exc = exc
            await asyncio.sleep(delay)

    raise last_exc  # unreachable, but satisfies type checker
