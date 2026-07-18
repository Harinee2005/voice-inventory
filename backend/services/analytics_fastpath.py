"""
Deterministic analytics fast-path.

Total-value questions ("what's the total value of my inventory?") are answered
with the exact Python-computed figures from the inventory summary — the LLM
never does arithmetic, eliminating an entire class of numeric hallucination.

Wired as a short-circuit at the top of aria_node (same pattern as the
affirmation short-circuit). Returns None whenever the question doesn't match,
so the normal LLM path handles anything more nuanced.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Total-value phrasings this fast-path owns. Anything else → LLM.
_TOTAL_VALUE_RE = re.compile(
    r"total\s+(inventory\s+)?value|total\s+inventory|inventory\s+(is\s+)?worth"
    r"|worth\s+of\s+(the\s+)?inventory|grand\s+total|inventory\s+value"
    r"|value\s+of\s+(my|the|our)\s+inventory|(everything|all)\s+worth",
    re.IGNORECASE,
)

_ALL_LOCATIONS_RE = re.compile(
    r"grand\s+total|all\s+locations?|across\s+all|every\s+location|overall|everything",
    re.IGNORECASE,
)


def _result(message: str, scope: str) -> dict:
    return {
        "message": message,
        "action": "query_result",
        "intent": "analytics",
        "data": {"items": [], "confirmed": False, "flags": []},
        "user_emotion": "neutral",
        "new_lexicons": [],
        "personality_note": None,
        "_fastpath_scope": scope,  # logging only — stripped by nothing, harmless extra key
    }


def try_analytics_fastpath(
    text: str,
    pre_classified_intent: str,
    inventory_summary: Optional[dict],
) -> Optional[dict]:
    """Return a complete aria_result dict for total-value questions, else None.

    Scope selection mirrors ARIA RULE 7 deterministically:
      all-locations keywords → grand total
      a named storage area   → that area's Python-computed sum
      otherwise              → active workspace total
    """
    if pre_classified_intent not in ("analytics", "query"):
        return None
    if not inventory_summary:
        return None
    if not _TOTAL_VALUE_RE.search(text):
        return None

    grand_total = inventory_summary.get("grand_total") or 0.0
    area_totals: dict = inventory_summary.get("area_totals") or {}
    workspace_label = inventory_summary.get("workspace_label")
    workspace_total = inventory_summary.get("workspace_total")

    # A) explicit all-locations phrasing → grand total
    if _ALL_LOCATIONS_RE.search(text):
        logger.info("ANALYTICS FASTPATH  scope=grand  total=%.2f", grand_total)
        return _result(
            f"The grand total across all locations is ${grand_total:.2f}.", "grand"
        )

    # B) a specific storage area named in the question → that area's sum.
    #    Longest label first so "Main Kitchen › Fridge" beats "Fridge".
    text_lower = text.lower()
    for key in sorted(area_totals, key=len, reverse=True):
        label, total = area_totals[key]
        if key in text_lower and (not workspace_label or key != workspace_label.lower()):
            logger.info("ANALYTICS FASTPATH  scope=area:%s  total=%.2f", label, total)
            return _result(f"The total value for {label} is ${total:.2f}.", f"area:{label}")

    # C) unscoped → active workspace total
    if workspace_label and workspace_total is not None:
        logger.info(
            "ANALYTICS FASTPATH  scope=workspace:%s  total=%.2f",
            workspace_label, workspace_total,
        )
        return _result(
            f"The total value for {workspace_label} is ${workspace_total:.2f}.",
            "workspace",
        )

    # No workspace set → fall back to grand total rather than guessing
    logger.info("ANALYTICS FASTPATH  scope=grand(no-workspace)  total=%.2f", grand_total)
    return _result(
        f"The total inventory value today is ${grand_total:.2f}.", "grand"
    )
