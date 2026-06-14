"""
Priority agent — BM25-based context pruning.

No LLM call. Runs between extraction_node and aria_node:
  - BM25-ranks conversation history turns against the current message
  - Keyword-filters inventory and item history to mentioned items
  - Strips unused lexicons from the user profile

For query/analytics intents, inventory filtering is skipped (worker might
ask about anything). History pruning always applies.
"""

import re
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

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


def rank_conversation_history(
    history: List[Dict],
    query: str,
    extra_terms: List[str] = None,
    top_k: int = 6,
) -> List[Dict]:
    """
    BM25-rank conversation history against (current message + item names).
    Always keeps the last 2 turns (immediately prior context is always relevant).
    Returns turns in original chronological order.
    """
    if not history:
        return []
    if len(history) <= top_k:
        return history

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
) -> dict:
    """
    Main entry point — returns filtered versions of every context section.

    Always prunes conversation history via BM25.
    Skips item filtering for query/analytics intents (full inventory needed).
    """
    target_items = _extract_target_items(intent_result, extraction_result)
    message_words = set(_tokenize(text))

    # 1. Conversation history — always BM25-pruned
    ranked_history = rank_conversation_history(
        history=conversation_history,
        query=text,
        extra_terms=target_items,
        top_k=6,
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
