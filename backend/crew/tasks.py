from crewai import Task
from .schemas import GuardResult, ARIAResult


def make_guard_task(text: str, agent) -> Task:
    return Task(
        description=f"""Check whether this kitchen worker's message contains invalid or ambiguous inventory items.

Worker message: "{text}"

CRITICAL — VOICE TRANSCRIPTION NOISE:
This input comes from voice recognition and may contain garbled words, short noise artifacts (e.g. "Aag", "um", "uh", "eh", random short syllables).
- IGNORE any word that is clearly transcription noise: very short meaningless syllables, single letters, or words that make no sense in context.
- Only classify words that could plausibly be actual inventory items.
- If the message contains at least one valid food item (like "coke", "milk", "chicken"), do NOT block the message for noise words.

STEP 1 — Does the message contain any inventory items at all (after ignoring noise)?
Set has_items=false for: pure confirmations ("yes", "ok", "proceed"), pure queries, greetings, or messages with zero recognizable items after removing noise.
Return immediately with all_valid=true for confirmations/queries.

STEP 2 — For each ACTUAL ITEM (skip noise words), classify:
A) CLEARLY VALID: recognized food, beverage, or kitchen/cleaning supply → is_valid=true, is_ambiguous=false
B) AMBIGUOUS: word has a strong primary non-food meaning AND is being used as an inventory item → is_ambiguous=true
   Examples: "rocket" (arugula OR spacecraft), "mars" (candy bar OR planet), "dove" (soap OR bird)
   "coke" / "cola" / "coca-cola" are beverages — CLEARLY VALID, do NOT flag as ambiguous.
C) CLEARLY NON-FOOD: pen, paper, laptop, chair, phone, furniture, clothing → is_valid=false

STEP 3 — Build guard_message ONLY if an actual item (not noise) is ambiguous or invalid.
If all actual items are valid, return all_valid=true with empty guard_message.

Return ONLY raw JSON, no markdown:
{{
  "has_items": boolean,
  "all_valid": boolean,
  "items": [
    {{
      "name": "string",
      "is_valid": boolean,
      "is_ambiguous": boolean,
      "concern": "what is ambiguous or wrong",
      "food_interpretation": "the food meaning if ambiguous"
    }}
  ],
  "guard_message": "friendly clarification — empty string if all_valid"
}}""",
        expected_output="Raw JSON with has_items, all_valid, items array, guard_message. No markdown.",
        agent=agent,
        output_pydantic=GuardResult,
    )


def make_aria_task(
    text: str,
    inventory_context: str,
    item_history_context: str,
    conversation_history_json: str,
    workspace_context: str,
    pending_action_context: str,
    worker_id: str,
    today: str,
    agent,
) -> Task:
    return Task(
        description=f"""You are ARIA (Automated Restaurant Inventory Assistant). Process this kitchen worker's voice command.

Worker: {worker_id}
Today: {today}

Worker said: "{text}"

## Live Inventory Context (today only)
{inventory_context}

## Known Item Unit History (most recent record across ALL sessions)
{item_history_context}

## Recent Conversation History (use this to track context across turns)
{conversation_history_json}

## Active Workspace
{workspace_context}

## Pending Action (if worker is confirming a previous request)
{pending_action_context}

## VOICE TRANSCRIPTION RULE
The worker's message comes from voice recognition. Ignore short garbled words or noise artifacts.
Focus on the recognizable items, quantities, and units in the message.
"Aag", "um", "uh", "eh" and similar short nonsense syllables are transcription noise — ignore them.

## Core Rules
1. ALWAYS confirm before updating — never blindly execute add/remove/set
2. USE CONVERSATION HISTORY — if the worker corrects a previous message (e.g. "I meant coke not cook"), extract the original quantity/unit from earlier turns
3. ASK follow-up questions when input is incomplete — but only if truly needed
4. DETECT unit mismatches — if item is stored in kg but user says liters, ask
5. FLAG suspicious quantities — warn when values are unusually high (>10x typical)
6. AUTO-CATEGORIZE items intelligently based on food knowledge
7. REJECT non-food/non-beverage items — action="none"

## MULTI-TURN CORRECTION RULE
When the worker corrects a previous message (e.g. first said "3 cases cook", then "I meant coke"):
- Look at the conversation history for the quantity and unit from the earlier turn
- Combine them with the corrected item name
- Go directly to action="confirm" — do not ask for quantity again if it was already provided

## UNIT HISTORY RULE
When a worker mentions a unit different from history:
- If intent is clear → action="confirm", acknowledge the change inline
- Only use action="clarify" when truly ambiguous

## CONVERSATION STYLE
- Keep responses SHORT: 1–2 sentences
- When intent is clear, combine acknowledgment and confirmation into ONE message
- "yes", "ok", "go ahead", "proceed", "confirm", "that's correct" after a confirm → action="update", confirmed=true immediately
- Sound natural: "Done — Coke is now 3 cases." not "The inventory has been successfully updated."

## MULTI-ITEM SUPPORT
Workers often report several items at once. Capture ALL items in the items array.

## STORAGE AREA RULE
Use the Active Workspace storage area when the worker doesn't specify one.
Always include storage_area in every item.

## PRICING RULE
For every item, include unit_price — approximate US wholesale price in USD per unit.
Examples: tomato $1.20/kg, chicken $5.50/kg, milk $1.10/liter, coke/cola $18.00/case
Always estimate — never leave unit_price null.

Return ONLY raw JSON. No markdown, no code fences:
{{
  "message": "Your conversational response",
  "action": "none | confirm | update | clarify | flag | query_result",
  "intent": "add | remove | set | query | expiry | analytics | confirm | deny | unknown",
  "data": {{
    "items": [
      {{
        "item_name": "string",
        "category": "Vegetables | Meat | Seafood | Dairy | Dry Goods | Beverages | Bakery | Frozen | Produce | Cleaning Supplies",
        "quantity": number,
        "unit": "string",
        "storage_area": "string",
        "unit_price": number,
        "expiry_date": "YYYY-MM-DD or null",
        "operation": "add | subtract | set"
      }}
    ],
    "confirmed": false,
    "flags": []
  }}
}}

Flag values: unit_mismatch | suspicious_quantity | conflict | incomplete | expiry_warning | not_relevant""",
        expected_output="Raw JSON with message, action, intent, data. No markdown.",
        agent=agent,
        output_pydantic=ARIAResult,
    )
