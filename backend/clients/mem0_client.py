"""
Mem0 client for voice-inventory.

Handles persistent worker memory: tone preferences, custom lexicons,
personality observations, and inventory patterns.

When MEM0_API_KEY is absent, all calls delegate to vector_memory_service
(local pgvector table) instead of the Mem0 cloud API.

Scopes used:
  - user  : per-worker preferences and insights, persists across sessions
  (thread scope is skipped — voice interactions are too short-lived)
"""

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_APP_ID = "voice-inventory"
_client = None

# When True: use local pgvector table; requires a db session passed to each call.
USE_LOCAL_VECTOR: bool = not bool(os.getenv("MEM0_API_KEY", "").strip())


def _get_client():
    """Lazy-init the sync Mem0 MemoryClient (thread-safe via asyncio.to_thread)."""
    global _client
    if _client is None:
        from mem0 import MemoryClient
        api_key = os.getenv("MEM0_API_KEY", "").strip()
        if not api_key:
            raise ValueError("MEM0_API_KEY not configured")
        _client = MemoryClient(api_key=api_key)
    return _client


def _enabled() -> bool:
    return bool(os.getenv("MEM0_API_KEY", "").strip())


def _extract_list(raw) -> list:
    """Normalise varying Mem0 response shapes to a plain list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "memories", "data"):
            v = raw.get(key)
            if isinstance(v, list):
                return v
    return []


async def get_user_memories(worker_id: str, db=None) -> list[dict]:
    """
    Retrieve all memories for a worker.
    Delegates to local pgvector table when USE_LOCAL_VECTOR=True (no MEM0_API_KEY).
    """
    if USE_LOCAL_VECTOR:
        if db is None:
            logger.warning("MEM0 get_user_memories: USE_LOCAL_VECTOR=True but db=None — returning empty")
            return []
        from services.vector_memory_service import get_worker_memories
        return await get_worker_memories(worker_id, db)

    if not _enabled():
        logger.debug("MEM0 get_user_memories SKIPPED (MEM0_API_KEY not set)  worker=%s", worker_id)
        return []
    t0 = time.perf_counter()
    logger.info("MEM0 GET ──▶  worker=%s", worker_id)
    try:
        client = _get_client()
        raw = await asyncio.to_thread(
            client.get_all, filters={"user_id": worker_id}
        )
        results = _extract_list(raw)
        logger.info("MEM0 GET ◀──  worker=%s  count=%d  elapsed=%.0fms", worker_id, len(results), (time.perf_counter() - t0) * 1000)
        return results
    except Exception as exc:
        logger.warning("MEM0 GET ERROR  worker=%s  elapsed=%.0fms  error=%s", worker_id, (time.perf_counter() - t0) * 1000, exc)
        return []


async def search_user_memories(query: str, worker_id: str, limit: int = 5, db=None) -> list[dict]:
    """
    Semantic search over a worker's memories.
    Delegates to local pgvector ANN when USE_LOCAL_VECTOR=True.
    """
    if USE_LOCAL_VECTOR:
        if db is None:
            logger.warning("MEM0 search_user_memories: USE_LOCAL_VECTOR=True but db=None — returning empty")
            return []
        from services.vector_memory_service import search_worker_memories
        return await search_worker_memories(worker_id, query, db, top_k=limit)

    if not _enabled():
        logger.debug("MEM0 search SKIPPED (MEM0_API_KEY not set)  worker=%s  query=%r", worker_id, query[:60])
        return []
    t0 = time.perf_counter()
    logger.info("MEM0 SEARCH ──▶  worker=%s  query=%r  limit=%d", worker_id, query[:80], limit)
    try:
        client = _get_client()
        raw = await asyncio.to_thread(
            client.search, query, filters={"user_id": worker_id}, top_k=limit
        )
        results = _extract_list(raw)
        logger.info("MEM0 SEARCH ◀──  worker=%s  results=%d  elapsed=%.0fms", worker_id, len(results), (time.perf_counter() - t0) * 1000)
        return results
    except Exception as exc:
        logger.warning("MEM0 SEARCH ERROR  worker=%s  elapsed=%.0fms  error=%s", worker_id, (time.perf_counter() - t0) * 1000, exc)
        return []


async def add_user_memory(content: str, worker_id: str, metadata: Optional[dict] = None, db=None) -> dict:
    """
    Add a memory for a worker.
    Delegates to local pgvector table when USE_LOCAL_VECTOR=True.
    """
    if USE_LOCAL_VECTOR:
        if db is None:
            logger.warning("MEM0 add_user_memory: USE_LOCAL_VECTOR=True but db=None — skipping")
            return {"written": False}
        from services.vector_memory_service import add_worker_memory
        memory_type = (metadata or {}).get("memory_type", "general")
        written = await add_worker_memory(worker_id, content, memory_type, db, metadata)
        return {"written": written}

    if not _enabled() or not (content or "").strip():
        logger.debug("MEM0 ADD SKIPPED  worker=%s  (disabled or empty content)", worker_id)
        return {}
    t0 = time.perf_counter()
    logger.info("MEM0 ADD ──▶  worker=%s  content=%r", worker_id, content[:200])
    try:
        client = _get_client()
        result = await asyncio.to_thread(
            client.add,
            content,
            user_id=worker_id,
            metadata={"app_id": _APP_ID, **(metadata or {})},
        )
        logger.info("MEM0 ADD ◀──  worker=%s  elapsed=%.0fms  result=%s", worker_id, (time.perf_counter() - t0) * 1000, bool(result))
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning("MEM0 ADD ERROR  worker=%s  elapsed=%.0fms  error=%s", worker_id, (time.perf_counter() - t0) * 1000, exc)
        return {}


def build_user_profile_from_memories(memories: list[dict]) -> str:
    """
    Convert memory records into a user profile context string for ARIA's system prompt.
    Works for both Mem0 cloud records and local vector_memory_service records.
    """
    if not memories:
        return ""
    lines = ["[Memory-based worker profile from previous sessions]:"]
    for m in memories:
        if isinstance(m, dict):
            text = (m.get("memory") or m.get("memory_text") or m.get("text") or "").strip()
            if text:
                lines.append(f"  • {text}")
    return "\n".join(lines) if len(lines) > 1 else ""
