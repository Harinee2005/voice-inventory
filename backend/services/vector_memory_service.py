"""
Local pgvector replacement for Mem0 worker memory.

Stores behavioural insights, tone preferences, and lexicon observations
per worker in a PostgreSQL table with HNSW vector index.

Used by mem0_client.py when MEM0_API_KEY is absent (USE_LOCAL_VECTOR=True).

Embeddings run locally via fastembed (ONNX runtime, no API key, no network
call) — Claude has no embeddings endpoint, so this replaces the OpenAI
text-embedding-3-small this table used to be sized for. The model weights
(~130MB) download once on first use and are cached under ~/.cache/fastembed.
"""

import asyncio
import logging
import math
import time
from functools import lru_cache
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMS = 384
_COSINE_DEDUP_THRESHOLD = 0.92
_SEARCH_LIMIT = 20


@lru_cache(maxsize=1)
def _get_embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=_EMBEDDING_MODEL)


@lru_cache(maxsize=512)
def _cached_embedding(text_key: str) -> list[float]:
    """Sync embedding call with LRU cache — avoids re-embedding identical strings."""
    vec = next(iter(_get_embedder().embed([text_key])))
    return vec.tolist()


async def get_embedding(text_input: str) -> list[float]:
    """Async wrapper around the cached sync embedding call."""
    key = text_input.strip()[:2000]
    return await asyncio.to_thread(_cached_embedding, key)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _vec_literal(embedding: list[float]) -> str:
    """Format a Python float list as a Postgres vector literal."""
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"


async def get_worker_memories(worker_id: str, db: Session) -> list[dict]:
    """Return all memory records for a worker (small enough to return all)."""
    t0 = time.perf_counter()
    try:
        from models import WorkerMemory
        rows = (
            db.query(WorkerMemory)
            .filter(WorkerMemory.worker_id == worker_id)
            .order_by(WorkerMemory.updated_at.desc())
            .all()
        )
        results = [{"memory": r.memory_text, "memory_type": r.memory_type, "id": r.id} for r in rows]
        logger.info(
            "VEC_MEM GET  worker=%s  count=%d  elapsed=%.0fms",
            worker_id, len(results), (time.perf_counter() - t0) * 1000,
        )
        return results
    except Exception as exc:
        logger.warning("VEC_MEM GET ERROR  worker=%s  error=%s", worker_id, exc)
        return []


async def search_worker_memories(worker_id: str, query_text: str, db: Session, top_k: int = 5) -> list[dict]:
    """Cosine ANN search over a worker's memories via pgvector HNSW index."""
    t0 = time.perf_counter()
    try:
        vec = await get_embedding(query_text)
        vec_str = _vec_literal(vec)
        rows = db.execute(text(
            "SELECT memory_text, memory_type, id, "
            "1 - (embedding <=> :vec) AS score "
            "FROM worker_memories "
            "WHERE worker_id = :wid AND embedding IS NOT NULL "
            "ORDER BY embedding <=> :vec "
            "LIMIT :k"
        ), {"vec": vec_str, "wid": worker_id, "k": top_k}).fetchall()

        results = [
            {"memory": r.memory_text, "memory_type": r.memory_type, "id": r.id, "score": float(r.score)}
            for r in rows
        ]
        logger.info(
            "VEC_MEM SEARCH  worker=%s  query=%r  results=%d  elapsed=%.0fms",
            worker_id, query_text[:60], len(results), (time.perf_counter() - t0) * 1000,
        )
        return results
    except Exception as exc:
        logger.warning("VEC_MEM SEARCH ERROR  worker=%s  error=%s", worker_id, exc)
        return []


async def add_worker_memory(
    worker_id: str,
    memory_text: str,
    memory_type: str,
    db: Session,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Add a memory for a worker. Skips if cosine similarity to any existing
    memory exceeds _COSINE_DEDUP_THRESHOLD (replaces Jaccard word-overlap dedup).
    """
    content = (memory_text or "").strip()
    if not content:
        return False

    t0 = time.perf_counter()
    try:
        vec = await get_embedding(content)
        vec_str = _vec_literal(vec)

        # Cosine dedup: fetch nearest existing memory for this worker
        row = db.execute(text(
            "SELECT memory_text, 1 - (embedding <=> :vec) AS score "
            "FROM worker_memories "
            "WHERE worker_id = :wid AND embedding IS NOT NULL "
            "ORDER BY embedding <=> :vec "
            "LIMIT 1"
        ), {"vec": vec_str, "wid": worker_id}).fetchone()

        if row and float(row.score) >= _COSINE_DEDUP_THRESHOLD:
            logger.info(
                "VEC_MEM ADD SKIPPED  worker=%s  similarity=%.3f  (near-duplicate)  content=%r",
                worker_id, float(row.score), content[:80],
            )
            return False

        from models import WorkerMemory
        db.add(WorkerMemory(
            worker_id=worker_id,
            memory_text=content,
            memory_type=memory_type,
            embedding=vec,
        ))
        db.commit()
        logger.info(
            "VEC_MEM ADD  worker=%s  type=%s  elapsed=%.0fms  content=%r",
            worker_id, memory_type, (time.perf_counter() - t0) * 1000, content[:80],
        )
        return True
    except Exception as exc:
        logger.warning("VEC_MEM ADD ERROR  worker=%s  error=%s", worker_id, exc)
        return False


def build_profile_from_memories(memories: list[dict]) -> str:
    """Format worker memory records as a context string for ARIA's system prompt."""
    if not memories:
        return ""
    lines = ["[Memory-based worker profile from previous sessions]:"]
    for m in memories:
        text = (m.get("memory") or m.get("memory_text") or "").strip()
        if text:
            lines.append(f"  • {text}")
    return "\n".join(lines) if len(lines) > 1 else ""
