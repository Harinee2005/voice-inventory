"""
Screen + Extract agent — one LLM call that replaces the former guard_agent
(food screening) and extraction_agent (strict structured parsing).

Runs after intent_node, before priority/aria. Screening fields drive the
rejected-path routing; extraction items are injected into ARIA's context.

Forces a single tool call (record_extraction) whose input_schema mirrors
ScreenedExtractionResult, then validates the tool call's input via Pydantic.
Not grammar-constrained decoding (client.messages.parse() / strict tool use)
— this schema's array-of-multi-enum-objects shape hits Claude's "Schema is
too complex" ceiling there. Plain tool use has no such ceiling, and Claude
follows the schema reliably in practice; validation still catches anything
that slips through — which is also why this prompt needs no schema echo or
enum lists.

Architecture note: pure extraction — no conversation, no recommendations.
Accuracy > completeness: missing data is acceptable, hallucinated data is not.
"""

import logging
import time
from typing import Any

from agents.schemas import ScreenedExtractionResult
from clients.llm_client import build_tool_schema, extract_tool_input, get_llm_client, get_llm_model

logger = logging.getLogger(__name__)

_TOOL_SCHEMA = build_tool_schema(ScreenedExtractionResult)


_SYSTEM_PROMPT = """\
You are a strict restaurant-inventory extraction and screening AI.
Your ONLY job: extract explicitly mentioned inventory items from a kitchen
worker's voice/text message, and judge whether each item is legitimate
food/beverage/kitchen inventory. You are NOT an assistant. Never infer,
never guess, never explain.

════ STEP 1 — IDENTIFY UNITS (before anything else) ════
Words in unit position are NOT items. A word is in unit position when it
follows a number ("5 [word] of coke") or names a measurement/packaging.
Unit typos (littles→liters, peices→pieces, botttles→bottles, grames→grams,
mililiters→ml, galons→gallons, pakkets→packets) are UNITS — never flag them
as invalid items. Valid spellings (litre, litres, kilo, kilos) are correct.

════ STEP 2 — VOICE NOISE FILTER ════
Input comes from speech recognition. Ignore garbled fragments and noise
("Aag", "um", "uh", stray syllables). If at least one valid food item
exists, do NOT block the message because of noise words.

════ STEP 3 — SCREEN EACH ITEM ════
For every extracted item set the screening fields:
  • Recognized food/beverage/kitchen/cleaning supply
      → is_food=true, is_ambiguous=false
  • Strong primary NON-food meaning used as inventory ("rocket" arugula/
    spacecraft, "mars" candy/planet, "dove" soap/bird)
      → is_ambiguous=true, concern=what is ambiguous,
        food_interpretation=the food meaning
    ("coke"/"cola" are clearly valid beverages — never ambiguous.)
  • Clearly non-food (pen, laptop, chair, phone, furniture, clothing,
    vehicles, machinery, tools, electronics, stationery)
      → is_food=false, concern=why it is not inventory

Top-level fields:
  has_items      — false for pure confirmations ("yes", "ok"), greetings,
                   pure queries, or nothing left after removing noise/units
  all_valid      — false only when an ACTUAL item is non-food or ambiguous
  guard_message  — friendly one-line clarification ONLY when all_valid=false
                   ("I can only track food and kitchen supplies — did you
                   mean something else by 'X'?"); empty string otherwise

════ STEP 4 — EXTRACT (strict, zero hallucination) ════
1. Extract ONLY items explicitly spoken. NEVER invent items, quantities,
   units, or categories. NEVER modify spoken values or merge items.
2. quantity missing → null. unit unclear → "UNKNOWN". Do NOT default
   ("oil" alone is NOT litres; "rice bags" has NO quantity).
3. raw_text = exact phrase spoken. canonical_name = normalized, singular,
   lowercase.
4. Hedged speech ("maybe 5 kg onion") → extract the value, confidence="LOW",
   requires_confirmation=true.
5. requires_confirmation=true whenever quantity is missing, the unit or
   item is unclear, speech is ambiguous, or confidence is LOW.
6. If a unit is physically wrong for the item (solid food in litres,
   liquid in kg): extract it EXACTLY as spoken with confidence="LOW" and
   requires_confirmation=true, validation_errors empty — correction is
   handled downstream.
7. validation_errors: only for negative quantities or genuinely unclear
   items. Empty array when valid.

════ CATALOG MATCHING ════
Prefer matching canonical items against this catalog; no safe match →
matched_catalog_item="UNKNOWN":
tomato, onion, potato, carrot, beans, cabbage, paneer, milk, curd, butter,
cheese, egg, chicken, mutton, fish, prawn, rice, basmati rice, sona masuri
rice, wheat flour, maida, cooking oil, sunflower oil, coconut oil, salt,
sugar, pepper, chilli powder, turmeric powder, garam masala, tea powder,
coffee powder, soft drink, water bottle, cleaning liquid, dish wash liquid,
garbage bag, aluminium foil

════ EXAMPLE ════
Input: "25 kg tomato and 2 motorbikes"
→ has_items=true, all_valid=false,
  guard_message="I can only track food and kitchen supplies — motorbikes
  aren't inventory items."
  items=[
    {raw_text:"25 kg tomato", canonical_name:"tomato",
     matched_catalog_item:"tomato", category:"vegetable", quantity:25,
     unit:"kg", confidence:"HIGH", requires_confirmation:false,
     validation_errors:[], is_food:true, is_ambiguous:false},
    {raw_text:"2 motorbikes", canonical_name:"motorbike",
     matched_catalog_item:"UNKNOWN", category:"UNKNOWN", quantity:2,
     unit:"UNKNOWN", confidence:"HIGH", requires_confirmation:true,
     validation_errors:[], is_food:false, is_ambiguous:false,
     concern:"vehicle, not food inventory"}
  ]

Accuracy > completeness. Missing data is acceptable; hallucinated data is not.\
"""


async def screen_and_extract(text: str) -> dict[str, Any]:
    """
    Screen + extract inventory items from a worker message in one LLM call.

    Returns a dict matching ScreenedExtractionResult.
    Falls back gracefully on any failure — never blocks the pipeline
    (all_valid=True lets the message through; ARIA parses raw text).
    """
    model = get_llm_model(screen=True)
    logger.info("SCREEN_EXTRACT ──▶  text=%r  model=%s", text[:120], model)
    t0 = time.perf_counter()
    try:
        from utils.llm_retry import call_llm
        response = await call_llm(
            lambda: get_llm_client().messages.create(
                model=model,
                max_tokens=2048,
                # cache_control on the system block also caches the tools
                # array above it (render order is tools → system → messages).
                # Below Haiku 4.5's 4096-token minimum today (~2.6K tokens
                # combined) — inert until this prompt grows past that.
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": f'Worker message: "{text}"'}],
                tools=[{
                    "name": "record_extraction",
                    "description": "Record the screened and extracted inventory items.",
                    "input_schema": _TOOL_SCHEMA,
                }],
                tool_choice={"type": "tool", "name": "record_extraction"},
            ),
            label="screen_extract",
            model=model,
        )
        tool_input = extract_tool_input(response)
        parsed = ScreenedExtractionResult.model_validate(tool_input) if tool_input else ScreenedExtractionResult()
        result = parsed.model_dump()
        elapsed = (time.perf_counter() - t0) * 1000
        items = result.get("items", [])
        logger.info(
            "SCREEN_EXTRACT ◀──  items=%d  has_items=%s  all_valid=%s  "
            "overall_confidence=%s  elapsed=%.0fms",
            len(items), result["has_items"], result["all_valid"],
            (result.get("inventory_session") or {}).get("overall_confidence"),
            elapsed,
        )
        for idx, it in enumerate(items):
            logger.info(
                "  [%d] raw=%r  canonical=%r  cat=%s  qty=%s  unit=%s  conf=%s  "
                "food=%s  ambiguous=%s%s",
                idx, it.get("raw_text", ""), it.get("canonical_name", ""),
                it.get("category", ""), it.get("quantity"), it.get("unit"),
                it.get("confidence"), it.get("is_food"), it.get("is_ambiguous"),
                f"  concern={it.get('concern')!r}" if it.get("concern") else "",
            )
        return result
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.warning(
            "SCREEN_EXTRACT ERROR  [FALLBACK]  elapsed=%.0fms  error_type=%s  error=%s "
            "→ letting message through with empty extraction (ARIA parses raw text)",
            elapsed, type(exc).__name__, exc,
        )
        return ScreenedExtractionResult().model_dump()
