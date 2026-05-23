"""
Guard agent — validates whether a worker's message contains legitimate
restaurant inventory items before passing it to ARIA.

Replaces the CrewAI Guard agent with a direct async OpenAI call.
"""

import json
import logging
import time

from agents.schemas import GuardResult
from clients.llm_client import get_llm_client, get_llm_model
from logging_config import J

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a strict food safety and inventory validator for restaurants and hotels.
Your job is to check whether items in a kitchen worker's voice message are legitimate
restaurant inventory items, flagging ambiguous or non-food items before they enter the database.

═══ STEP 0 — IDENTIFY UNITS FIRST (before classifying items) ═══
Words in a UNIT POSITION must NOT be classified as items.
A word is in unit position when it follows a number ("5 [word] of coke") or sounds like
any measurement (weight, volume, count, packaging).

Common unit typos — treat as UNITS, not items:
  littles/litters → liters | kilo/kilos → kg | peices/pices → pieces
  botttles/botles → bottles | canes/caans → cans | grames → grams
  mililiters/mls → ml | galons → gallons | pakkets/packt → packets
Do NOT flag unit typos as invalid items.

═══ STEP 1 — VOICE NOISE FILTER ═══
Input is from voice recognition — ignore garbled words, short noise artifacts
("Aag", "um", "uh", random short syllables). If at least one valid food item exists,
do NOT block on noise words.

═══ STEP 2 — has_items check ═══
Set has_items=false for: pure confirmations ("yes", "ok"), pure queries, greetings,
or messages with zero recognizable items after removing noise and units.
Return all_valid=true immediately for confirmations/queries.

═══ STEP 3 — Classify each ACTUAL ITEM ═══
A) CLEARLY VALID: recognized food, beverage, or kitchen/cleaning supply → is_valid=true, is_ambiguous=false
B) AMBIGUOUS: word has a strong primary non-food meaning AND is used as inventory → is_ambiguous=true
   Examples: "rocket" (arugula OR spacecraft), "mars" (candy bar OR planet), "dove" (soap OR bird)
   "coke"/"cola"/"coca-cola" → CLEARLY VALID beverages, never ambiguous
C) CLEARLY NON-FOOD: pen, paper, laptop, chair, phone, furniture, clothing,
   vehicles (bike, motorbike, car, truck, scooter, motor), machinery, tools,
   electronics, stationery → is_valid=false

═══ STEP 4 — guard_message ═══
Only build guard_message when an actual item (not a unit, not noise) is ambiguous or invalid.
If all actual items are valid, return all_valid=true with empty guard_message.

Return ONLY raw JSON, no markdown:
{
  "has_items": boolean,
  "all_valid": boolean,
  "items": [
    {
      "name": "string",
      "is_valid": boolean,
      "is_ambiguous": boolean,
      "concern": "what is ambiguous or wrong",
      "food_interpretation": "the food meaning if ambiguous"
    }
  ],
  "guard_message": "friendly clarification — empty string if all_valid"
}\
"""


async def guard_validate(text: str) -> dict:
    """
    Validate whether the message contains legitimate inventory items.
    Returns a dict matching GuardResult schema.
    """
    model = get_llm_model()
    logger.info("GUARD ──▶  text=%r  model=%s", text[:120], model)
    t0 = time.perf_counter()
    try:
        response = await get_llm_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'Worker message: "{text}"'},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        parsed = GuardResult.model_validate_json(content)
        result = parsed.model_dump()
        elapsed = (time.perf_counter() - t0) * 1000
        item_names = [i["name"] for i in result.get("items", [])]
        logger.info(
            "GUARD ◀──  has_items=%s  all_valid=%s  items=%s  guard_msg=%r  elapsed=%.0fms",
            result["has_items"], result["all_valid"], item_names,
            result.get("guard_message", "")[:80], elapsed,
        )
        if not result["all_valid"]:
            for it in result.get("items", []):
                if not it.get("is_valid") or it.get("is_ambiguous"):
                    logger.warning(
                        "GUARD FLAGGED  item=%r  valid=%s  ambiguous=%s  concern=%r",
                        it["name"], it["is_valid"], it["is_ambiguous"], it.get("concern", ""),
                    )
        return result
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.warning(
            "GUARD ERROR  [FALLBACK]  elapsed=%.0fms  error_type=%s  error=%s "
            "→ all_valid=True (letting message through)",
            elapsed, type(exc).__name__, exc,
        )
        return GuardResult(has_items=False, all_valid=True).model_dump()
