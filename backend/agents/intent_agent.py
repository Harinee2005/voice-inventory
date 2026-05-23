"""
Dedicated intent classifier — runs before ARIA on every turn.

Architecture rationale:
  Asking one LLM call to simultaneously classify intent, parse entities,
  generate a response, and compute personality notes produces inconsistent
  intent labels because intent is a side-effect, not a primary output.
  A cheap, focused gpt-4o-mini call with temperature=0 classifies intent
  consistently, and injects it as a hard constraint into ARIA's prompt.

Key patterns:
  - Strict JSON schema via completions.parse() + IntentResult Pydantic model
    → LLM cannot produce "add_item" instead of "add"
  - Chain-of-thought via the `reasoning` field before the label
  - Closed-world assumption: exactly 9 allowed intents, no others
  - Context-aware: pending action + recent history injected for slot inheritance
  - Confidence threshold: < 0.65 triggers clarification, not ARIA
  - Graceful fallback: any failure returns intent=unknown (never blocks)
"""

import json
import logging
import time
from typing import Any

from agents.schemas import IntentResult
from clients.llm_client import get_llm_client, get_llm_model
from logging_config import J

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a precise intent classifier for a restaurant kitchen inventory voice assistant.
Your ONLY job is to classify the worker's intent from their spoken message.
Do NOT generate inventory responses. Do NOT confirm or deny actions yourself.

═══ ALLOWED INTENTS (use exactly one — no variations) ═══
  add       — worker wants to add/increase stock
              e.g. "add 5 kg chicken", "we got 10 liters milk", "received 20 bottles"
  remove    — worker wants to remove/decrease stock
              e.g. "use 2 kg beef", "remove 3 bottles", "take out 1 kg rice"
  set       — worker wants to set an exact quantity
              e.g. "set chicken to 10 kg", "chicken is 5 kg", "make it 3 bottles"
  query     — asking what quantity is in stock
              e.g. "how much chicken", "what do we have", "check beef stock"
  expiry    — asking about or setting expiry dates
              e.g. "when does milk expire", "beef expires friday", "check expiry"
  analytics — asking for totals, values, statistics
              e.g. "total value", "grand total", "what's cold storage worth", "all locations"
  confirm   — worker is confirming/saying yes to a pending action
              e.g. "yes", "ok", "go ahead", "yep", "sure", "do it", "that's right"
  deny      — worker is cancelling/saying no to a pending action
              e.g. "no", "cancel", "never mind", "stop", "don't", "wrong"
  unknown   — cannot determine intent with confidence ≥ 0.65

═══ CLASSIFICATION RULES ═══
1. PENDING ACTION FIRST: If a pending action exists, single words like "yes/ok/sure/go/
   proceed/correct/right/yep/yeah/do it" → intent=confirm (confidence=1.0).
2. HISTORY INHERITANCE: If last 3 turns show a build-up (e.g. "add 5 kg" then "of chicken"),
   the current message inherits the prior intent. "5 kg" alone after "add chicken" → confirm.
3. CORRECTIONS: "no I meant chicken not beef" → inherit original intent (not deny).
   Only "no/cancel/never mind" with no subject → deny.
4. VOICE NOISE: Ignore garbled words. "add 5 kg" + background noise word → still add.
5. NUMBERS ONLY: "5 kg", "10 pieces" with a pending add/remove → intent=confirm.
6. UNIT CORRECTIONS: "actually liters not kg" with a pending action → intent=confirm
   (user is correcting a unit, not cancelling).
7. CLASSIFY THE VERB, NOT THE ITEM: Your job is to classify the action verb only.
   If the message contains a clear action verb (add/remove/set/check), classify it
   as that intent — even if the item seems non-food or invalid (e.g. "add 2 motor bikes").
   Do NOT return unknown just because the item seems wrong. Item validity is checked
   downstream by the guard. Return unknown ONLY when the action itself is unclear.

═══ CONFIDENCE SCORING ═══
  1.0  — unambiguous ("add 5 kg chicken breast")
  0.85 — clear with minor voice noise ("add fife kg chicken" → five kg)
  0.70 — probable but some uncertainty
  0.65 — threshold — set needs_clarification=true below this
  0.50 — genuinely ambiguous — needs_clarification=true, write clarification_question
  0.0  — cannot classify at all

═══ SLOT EXTRACTION ═══
If present, extract:
  slot_item     — the food/beverage item name as spoken
  slot_quantity — numeric quantity (convert words: "five" → 5.0)
  slot_unit     — unit of measurement as spoken (kg, liters, pieces, etc.)

If a slot value is not present in the current message or cannot be inferred
from the last 3 turns, leave it null. Never guess or fabricate slots.

═══ FEW-SHOT EXAMPLES ═══
Worker: "add 5 kg of chicken breast"
→ {"intent":"add","confidence":1.0,"reasoning":"explicit add verb, qty=5, unit=kg, item=chicken breast","slot_item":"chicken breast","slot_quantity":5.0,"slot_unit":"kg","needs_clarification":false,"clarification_question":null}

Worker: "we received 20 liters of cooking oil"
→ {"intent":"add","confidence":0.95,"reasoning":"'received' implies stock addition","slot_item":"cooking oil","slot_quantity":20.0,"slot_unit":"liters","needs_clarification":false,"clarification_question":null}

Worker: "use 2 kg beef for tonight"
→ {"intent":"remove","confidence":0.95,"reasoning":"'use' = consuming from stock = remove","slot_item":"beef","slot_quantity":2.0,"slot_unit":"kg","needs_clarification":false,"clarification_question":null}

Worker: "set salmon to 3 kg"
→ {"intent":"set","confidence":1.0,"reasoning":"explicit set command with target quantity","slot_item":"salmon","slot_quantity":3.0,"slot_unit":"kg","needs_clarification":false,"clarification_question":null}

Worker: "how much chicken do we have"
→ {"intent":"query","confidence":1.0,"reasoning":"asking about current stock quantity","slot_item":"chicken","slot_quantity":null,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "what's the total value of cold storage"
→ {"intent":"analytics","confidence":0.95,"reasoning":"asking for monetary total of a storage area","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "yes" (pending action: add 5 kg chicken)
→ {"intent":"confirm","confidence":1.0,"reasoning":"single affirmation word with pending add action","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "no cancel that"
→ {"intent":"deny","confidence":1.0,"reasoning":"explicit cancellation phrase","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "5 kg" (pending action: add chicken)
→ {"intent":"confirm","confidence":0.90,"reasoning":"quantity-only response to pending add — completing the unit","slot_item":null,"slot_quantity":5.0,"slot_unit":"kg","needs_clarification":false,"clarification_question":null}

Worker: "no I meant liters not kg" (pending action: add milk in kg)
→ {"intent":"confirm","confidence":0.88,"reasoning":"unit correction, not cancellation — worker still wants to add milk, just changing unit","slot_item":null,"slot_quantity":null,"slot_unit":"liters","needs_clarification":false,"clarification_question":null}

Worker: "when does the milk expire"
→ {"intent":"expiry","confidence":1.0,"reasoning":"asking about expiry date","slot_item":"milk","slot_quantity":null,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "add 2 motor bikes"
→ {"intent":"add","confidence":1.0,"reasoning":"clear 'add' verb — item validity is not my job, guard checks downstream","slot_item":"motor bikes","slot_quantity":2.0,"slot_unit":null,"needs_clarification":false,"clarification_question":null}

Worker: "wow great find"
→ {"intent":"unknown","confidence":0.10,"reasoning":"casual social exclamation — no inventory action, item, or quantity","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":true,"clarification_question":"Thanks! What would you like to manage today?"}

Worker: "hey good morning"
→ {"intent":"unknown","confidence":0.10,"reasoning":"greeting with no inventory intent","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":true,"clarification_question":"Good morning! What would you like to add, remove, or check?"}

Worker: "um the thing from yesterday"
→ {"intent":"unknown","confidence":0.30,"reasoning":"too vague to classify — no item, qty, or action verb","slot_item":null,"slot_quantity":null,"slot_unit":null,"needs_clarification":true,"clarification_question":"What would you like to do? For example: add, remove, or check stock?"}

Return ONLY raw JSON — no markdown, no code fences.\
"""


async def classify_intent(
    text: str,
    conversation_history: list[dict[str, Any]],
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Classify intent from worker message with context.

    Returns a dict matching IntentResult schema.
    Falls back gracefully to intent=unknown on any failure.
    """
    model = get_llm_model(intent=True)

    # Last 6 messages = 3 user + 3 assistant turns (enough for slot inheritance)
    recent = conversation_history[-6:]
    history_text = "\n".join(
        f"  {m['role'].upper()}: {m['content']}" for m in recent
    ) or "  (no history yet)"

    pending_text = ""
    if pending_action:
        items = pending_action.get("items", [])
        if items:
            summary = ", ".join(
                f"{i.get('quantity')} {i.get('unit','').strip()} {i.get('item_name','')}"
                for i in items[:3]
                if i.get("item_name")
            )
            pending_text = f"\nPending action awaiting confirmation: {summary}"

    user_msg = (
        f"Recent conversation:\n{history_text}"
        f"{pending_text}\n\n"
        f'Current worker message: "{text}"\n\n'
        "Classify the intent. Think step-by-step in the reasoning field first, "
        "then commit to the intent label."
    )

    logger.info(
        "INTENT ──▶  text=%r  pending=%s  history_turns=%d  model=%s",
        text[:120], bool(pending_action), len(recent), model,
    )
    t0 = time.perf_counter()
    try:
        response = await get_llm_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=400,
        )
        content = response.choices[0].message.content
        parsed = IntentResult.model_validate_json(content)
        result = parsed.model_dump()
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            "INTENT ◀──  intent=%s  confidence=%.2f  needs_clarification=%s  "
            "slot_item=%r  slot_qty=%s  slot_unit=%r  elapsed=%.0fms",
            result["intent"], result["confidence"], result["needs_clarification"],
            result.get("slot_item"), result.get("slot_quantity"), result.get("slot_unit"),
            elapsed,
        )
        logger.debug("INTENT reasoning=%r", result.get("reasoning", ""))
        return result

    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.warning(
            "INTENT ERROR  [FALLBACK]  elapsed=%.0fms  error_type=%s  error=%s "
            "→ returning intent=unknown",
            elapsed, type(exc).__name__, exc,
        )
        return IntentResult(
            intent="unknown",
            confidence=0.0,
            reasoning=f"classifier error: {exc}",
            needs_clarification=False,
        ).model_dump()
