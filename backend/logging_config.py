"""
Centralized logging for voice-inventory.

Every log line carries a trace_id so you can grep one request end-to-end:

    grep "a3f9c1b2" aria_output.log

Format:
    2026-05-23 18:50:31.340  INFO   [a3f9c1b2]  [module                 ]  message

Call setup_logging() once at startup (main.py lifespan).
Set the trace_id at every request entry point (HTTP middleware / chat service).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import uuid
from contextvars import ContextVar
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).parent
ROOT_DIR = _BACKEND_DIR.parent
LOG_FILE = ROOT_DIR / "aria_output.log"

# ── Per-request trace ID ──────────────────────────────────────────────────────
# Set this at the HTTP middleware or chat-service entry, then every log line
# in the same async task automatically carries the same ID.
trace_id: ContextVar[str] = ContextVar("trace_id", default="--------")


def new_trace() -> str:
    """Generate and activate a fresh 8-char trace ID. Returns the ID."""
    tid = uuid.uuid4().hex[:8]
    trace_id.set(tid)
    return tid


def get_trace() -> str:
    return trace_id.get("--------")


# ── Custom filter — injects trace_id into every log record ───────────────────
class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id.get("--------")  # type: ignore[attr-defined]
        return True


# ── Format ────────────────────────────────────────────────────────────────────
_FMT  = "%(asctime)s.%(msecs)03d  %(levelname)-8s  [%(trace_id)s]  [%(name)-24s]  %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"

# ── Noisy third-party loggers ─────────────────────────────────────────────────
_QUIET = {
    "httpx":               logging.WARNING,
    "httpcore":            logging.WARNING,
    "openai":              logging.WARNING,
    "openai._base_client": logging.WARNING,
    "uvicorn.access":      logging.WARNING,
    "uvicorn.error":       logging.INFO,
    "sqlalchemy.engine":   logging.WARNING,
    "sqlalchemy.pool":     logging.WARNING,
    "langgraph":           logging.WARNING,
    "mem0":                logging.WARNING,
    "mem0.client":         logging.WARNING,
}


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger.  Safe to call multiple times.

    Writes to:
      • aria_output.log  (rotating, 10 MB × 5 backups)
      • stderr           (console mirror)
    """
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(level)

    tf = _TraceFilter()
    formatter = logging.Formatter(_FMT, datefmt=_DATE)

    # Rotating file
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    fh.setLevel(level)
    fh.addFilter(tf)
    root.addHandler(fh)

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.setLevel(logging.INFO)
    ch.addFilter(tf)
    root.addHandler(ch)

    for name, lvl in _QUIET.items():
        logging.getLogger(name).setLevel(lvl)

    root.info(
        "═══ LOGGING STARTED ═══  file=%s  level=%s",
        LOG_FILE, logging.getLevelName(level),
    )


# ── Utilities ─────────────────────────────────────────────────────────────────

def J(obj: object, max_len: int = 600) -> str:
    """Compact single-line JSON for inline log messages, truncated at max_len."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        s = repr(obj)
    return s if len(s) <= max_len else s[:max_len] + "…"


def separator(label: str = "") -> str:
    """Return a visual separator line for turn boundaries in the log."""
    if label:
        return f"{'─' * 20} {label} {'─' * 20}"
    return "─" * 60
