"""
Priority agent — context pruning via BM25 (fallback) or pgvector ANN (preferred).

No LLM call. Runs between screen_extract_node and aria_node:
  - Ranks conversation history turns against the current message
  - Keyword-filters inventory and item history to mentioned items
  - Strips unused lexicons from the user profile

For query/analytics intents, inventory filtering is skipped (worker might
ask about anything). History pruning always applies.

When pgvector is available (DATABASE_URL starts with postgresql), conversation
history ranking uses cosine ANN over turn_embedding instead of BM25 term-overlap.
"""

import os
import re
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

_USE_PGVECTOR = os.getenv("DATABASE_URL", "").startswith("postgresql")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "is", "it", "of", "in", "to",
    "do", "i", "we", "for", "some", "any", "this", "that", "with",
    "please", "can", "you", "me", "my", "our", "how", "what",
}

# Intents where we skip item-level inventory filtering (need full picture)
_BROAD_INTENTS = {"query", "analytics"}


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _extract_target_items(intent_result: dict, extraction_result: dict) -> List[str]:
    """Collect item names from intent slots + extraction agent output."""
    items: List[str] = []
    slot = (intent_result or {}).get("slot_item")
    if slot:
        items.append(slot)
    for ei in ((extraction_result or {}).get("items") or []):
        name = ei.get("canonical_name") or ei.get("raw_text")
        if name and name not in items:
            items.append(name)
    return items


def _rank_with_bm25(history: List[Dict], query: str, extra_terms: List[str], top_k: int) -> List[Dict]:
    """BM25 fallback ranking — in-memory, no DB needed."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("priority  rank_bm25 not installed — falling back to last %d turns", top_k)
        return history[-top_k:]

    docs = [_tokenize(h.get("content", "")) for h in history]
    query_tokens = _tokenize(query)
    for term in (extra_terms or []):
        query_tokens.extend(_tokenize(term))

    must_include = set(range(max(0, len(history) - 2), len(history)))
    selected = set(must_include)
    if query_tokens and any(docs):
        scores = BM25Okapi(docs).get_scores(query_tokens)
        for i in sorted(range(len(scores)), key=lambda x: scores[x], reverse=True):
            if len(selected) >= top_k:
                break
            selected.add(i)
    else:
        for i in range(len(history) - 1, -1, -1):
            if len(selected) >= top_k:
                break
            selected.add(i)

    result = [history[i] for i in sorted(selected)]
    logger.info(
        "priority  history  BM25 %d→%d turns  (dropped %d)",
        len(history), len(result), len(history) - len(result),
    )
    return result


def _rank_with_pgvector(
    history: List[Dict],
    query: str,
    extra_terms: List[str],
    top_k: int,
    session_id: str,
    db: Any,
) -> List[Dict]:
    """
    pgvector ANN ranking — embeds the query, fetches top-k turns from the DB,
    merges with the 2 most recent turns for recency bias, returns as dicts.
    """
    try:
        from sqlalchemy import text as sa_text
        import asyncio
        from services.vector_memory_service import get_embedding as _get_embedding

        query_full = query + " " + " ".join(extra_terms or [])

        # get_embedding is async — run it in the current event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _get_embedding(query_full))
                vec = future.result(timeout=5)
        else:
            vec = loop.run_until_complete(_get_embedding(query_full))

        vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

        rows = db.execute(sa_text(
            "SELECT role, content, timestamp "
            "FROM conversations "
            "WHERE session_id = :sid AND turn_embedding IS NOT NULL "
            "ORDER BY turn_embedding <=> :vec "
            "LIMIT :k"
        ), {"sid": session_id, "vec": vec_str, "k": top_k}).fetchall()

        ann_turns = [
            {"role": r.role, "content": r.content, "timestamp": str(r.timestamp)}
            for r in rows
        ]

        # Always include the 2 most recent turns from the pre-loaded list
        recency_turns = history[-2:] if len(history) >= 2 else history

        # Merge: ann_turns + recency, deduplicate by content
        seen_content = {t["content"] for t in ann_turns}
        merged = list(ann_turns)
        for t in recency_turns:
            if t.get("content") not in seen_content:
                merged.append(t)
                seen_content.add(t.get("content", ""))

        merged = merged[:top_k]
        logger.info(
            "priority  history  pgvector ANN %d→%d turns  (session=%s)",
            len(history), len(merged), session_id,
        )
        return merged

    except Exception as exc:
        logger.warning("priority  pgvector rank failed (%s) — falling back to BM25", exc)
        return _rank_with_bm25(history, query, extra_terms, top_k)


def rank_conversation_history(
    history: List[Dict],
    query: str,
    extra_terms: List[str] = None,
    top_k: int = 6,
    db: Any = None,
    session_id: str = "",
) -> List[Dict]:
    """
    Rank conversation history turns against the current message.
    Uses pgvector ANN when db + session_id are provided and DATABASE_URL is PostgreSQL.
    Falls back to BM25 otherwise.
    Always keeps the last 2 turns. Returns turns in chronological order.
    """
    if not history:
        return []
    if len(history) <= top_k:
        return history

    if _USE_PGVECTOR and db is not None and session_id:
        return _rank_with_pgvector(history, query, extra_terms or [], top_k, session_id, db)

    return _rank_with_bm25(history, query, extra_terms or [], top_k)


def _filter_context_lines(context: str, item_names: List[str], bullet: str = "•") -> tuple[str, int, int]:
    """
    Filter bullet-point lines in a context string to only those matching item_names.
    Non-bullet lines (headers, summaries) are always kept.
    Returns (filtered_text, kept_count, total_count).
    """
    names_lower = [n.lower() for n in item_names if n]
    lines = context.split('\n')
    result = []
    kept = total = 0

    for line in lines:
        stripped = line.strip()
        is_item = stripped.startswith(bullet)
        # Workspace detail lines use '-' with '×' (price calculation rows)
        is_sub = stripped.startswith('-') and '×' in stripped
        if is_item or is_sub:
            total += 1
            if any(name in line.lower() for name in names_lower):
                result.append(line)
                kept += 1
        else:
            result.append(line)

    return '\n'.join(result), kept, total


def filter_inventory_context(inventory_context: str, item_names: List[str]) -> str:
    if not item_names or not inventory_context:
        return inventory_context
    filtered, kept, total = _filter_context_lines(inventory_context, item_names)
    logger.info("priority  inventory  kept %d/%d item rows  targets=%s", kept, total, item_names)
    return filtered


def filter_item_history_context(item_history: str, item_names: List[str]) -> str:
    if not item_names or not item_history:
        return item_history
    filtered, kept, total = _filter_context_lines(item_history, item_names)
    logger.info("priority  item_history  kept %d/%d rows  targets=%s", kept, total, item_names)
    return filtered


def filter_profile_context(profile_context: str, message_words: set) -> str:
    """
    Pass through all non-lexicon lines.
    For lexicon lines ('• word → resolved'), only keep entries whose
    original_word appears in (or is a substring of) words from the message.
    """
    if not profile_context:
        return profile_context

    lines = profile_context.split('\n')
    result = []
    in_lexicon_section = False
    kept = total = 0

    for line in lines:
        stripped = line.strip()
        if 'known lexicons' in stripped.lower():
            in_lexicon_section = True
            result.append(line)
            continue

        if in_lexicon_section and stripped.startswith("•"):
            total += 1
            m = re.search(r"'([^']+)'", stripped)
            if m:
                word = m.group(1).lower()
                if word in message_words or any(word in w for w in message_words):
                    result.append(line)
                    kept += 1
            # If no match, drop the lexicon line
            continue

        result.append(line)

    if total:
        logger.info("priority  profile  lexicons kept %d/%d", kept, total)
    return '\n'.join(result)


def prioritize_context(
    text: str,
    intent: str,
    intent_result: dict,
    extraction_result: dict,
    conversation_history: List[Dict],
    inventory_context: str,
    item_history_context: str,
    user_profile_context: str,
    db: Any = None,
    session_id: str = "",
) -> dict:
    """
    Main entry point — returns filtered versions of every context section.

    Prunes conversation history via pgvector ANN (when db/session_id provided) or BM25.
    Skips item filtering for query/analytics intents (full inventory needed).
    """
    target_items = _extract_target_items(intent_result, extraction_result)
    message_words = set(_tokenize(text))

    # 1. Conversation history — pgvector ANN preferred, BM25 fallback
    ranked_history = rank_conversation_history(
        history=conversation_history,
        query=text,
        extra_terms=target_items,
        top_k=6,
        db=db,
        session_id=session_id,
    )

    # 2. Inventory + item history — skip item filter for broad intents
    broad = intent in _BROAD_INTENTS or not target_items
    if broad:
        filtered_inventory = inventory_context
        filtered_item_history = item_history_context
    else:
        filtered_inventory = filter_inventory_context(inventory_context, target_items)
        filtered_item_history = filter_item_history_context(item_history_context, target_items)

    # 3. User profile — lexicon filter always applies
    filtered_profile = filter_profile_context(user_profile_context, message_words)

    focus = f"items={target_items}" if target_items else "no specific item"
    logger.info(
        "priority  DONE  intent=%s  focus=%s  history=%d→%d  item_filter=%s",
        intent, focus, len(conversation_history), len(ranked_history), not broad,
    )

    return {
        "priority_conversation_history": ranked_history,
        "priority_inventory_context": filtered_inventory,
        "priority_item_history_context": filtered_item_history,
        "priority_profile_context": filtered_profile,
        "priority_focus": focus,
    }
