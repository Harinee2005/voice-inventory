"""
Circuit breaker for Claude API calls.

States:
  closed   → normal operation, all calls go through
  open     → failing fast (3+ consecutive failures), raises immediately
  half-open → one probe call allowed to test if the service recovered

Usage (via call_llm in llm_retry.py — agents don't need to touch this directly):
  breaker = get_breaker()
  async with breaker:
      response = await client.create(...)
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 3   # consecutive failures before opening
_RESET_TIMEOUT    = 60.0 # seconds before half-open probe


class ServiceUnavailableError(RuntimeError):
    """Raised when the circuit breaker is open."""


class CircuitBreaker:
    def __init__(self, failure_threshold: int = _FAILURE_THRESHOLD, reset_timeout: float = _RESET_TIMEOUT):
        self._threshold   = failure_threshold
        self._timeout     = reset_timeout
        self._failures    = 0
        self._state       = "closed"   # closed | open | half-open
        self._opened_at   = 0.0
        self._lock        = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    async def __aenter__(self):
        async with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self._timeout:
                    self._state = "half-open"
                    logger.info("CIRCUIT BREAKER  half-open (probing)")
                else:
                    remaining = int(self._timeout - (time.monotonic() - self._opened_at))
                    raise ServiceUnavailableError(
                        f"Circuit breaker is open — retrying in ~{remaining}s"
                    )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        async with self._lock:
            if exc_type is None:
                # Success — reset
                if self._state == "half-open":
                    logger.info("CIRCUIT BREAKER  closed (service recovered)")
                self._failures = 0
                self._state    = "closed"
            else:
                # Failure — accumulate
                self._failures += 1
                if self._state == "half-open" or self._failures >= self._threshold:
                    if self._state != "open":
                        logger.warning(
                            "CIRCUIT BREAKER  OPEN  failures=%d  will retry in %.0fs",
                            self._failures, self._timeout,
                        )
                    self._state     = "open"
                    self._opened_at = time.monotonic()
        return False  # do not suppress the exception


# Module-level singleton — one breaker for the Claude API
_breaker: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
