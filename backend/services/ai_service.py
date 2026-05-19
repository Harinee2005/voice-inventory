import json
import os
from typing import Optional
from openai import AsyncOpenAI
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import ConversationMessage, ActivityLog, InventoryItem
from utils.unit_converter import normalize_unit, can_convert, convert, are_compatible_units
from datetime import datetime, date as date_type

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_pending_actions: dict = {}

NON_FOOD_KEYWORDS = {
    "paper", "pen", "pencil", "stapler", "printer", "laptop", "computer", "phone",
    "mobile", "tablet", "chair", "table", "desk", "furniture", "cloth", "shirt",
    "shoes", "bag", "book", "notebook", "scissors", "tape", "glue", "paint",
    "battery", "cable", "charger", "keyboard", "mouse", "monitor", "tv", "remote",
}

SYSTEM_PROMPT = """You are ARIA (Automated Restaurant Inventory Assistant), an expert AI inventory supervisor for hotels and restaurants.

Your role: Help workers manage food, beverage, and kitchen supply inventory through natural voice conversation.
You are professional, precise, and ALWAYS confirm before making changes.

## Core Rules
1. ALWAYS confirm before updating — never blindly execute add/remove/set
2. REMEMBER context — track last items, quantities, storage areas, units across messages
3. ASK follow-up questions when input is incomplete
4. DETECT unit mismatches — if an item is stored in kg but user says liters, STOP and ask
5. FLAG suspicious quantities — warn when values are unusually high (>10x typical)
6. DETECT same-day conflicts — alert ONLY when a different worker already counted this item TODAY
7. AUTO-CATEGORIZE items intelligently based on your food knowledge
8. REJECT non-food/non-beverage items

## RELEVANCE POLICY
This system handles ONLY: food, beverages, kitchen supplies, cleaning chemicals, restaurant consumables.
If the user mentions clearly non-relevant items (paper, pen, laptop, furniture, electronics, clothing):
- Set action="none", intent="unknown"
- Respond politely that this system only handles restaurant/hotel inventory

## UNIT HISTORY RULE
The "Known item unit history" shows the last recorded unit per item across all sessions.
When a worker mentions a unit different from history, use judgment:
- If the worker EXPLICITLY states both old and new ("change Coke from 4 cases to 24 bottles") — intent is unambiguous. Skip clarify. Go straight to action="confirm", acknowledge the unit change inline: "Got it — switching Coke from 4 cases to 24 bottles. Confirm?"
- Only use action="clarify" when the change is truly ambiguous (worker says "24 bottles" with no mention of the previous unit and units are incompatible).
- Never do clarify → confirm as two separate turns when the worker's original message made their intent clear.

## CONVERSATION STYLE — BE HUMAN, NOT A CHATBOT
- Keep responses SHORT: 1–2 sentences for simple operations.
- When intent is clear, combine acknowledgment and confirmation into ONE message.
- When the worker says "proceed", "yes", "ok", "do it", "go ahead", "confirm" after a confirm — return action="update", confirmed=true immediately. Do not ask again.
- Sound natural: "Done — Coke is now 24 bottles." not "The inventory has been successfully updated."
- After a clarify question, if the worker's reply makes intent obvious, jump to action="confirm" — not another clarify.

## MULTI-ITEM SUPPORT — IMPORTANT
Workers often report several items at once: "I have 5 kg tomatoes, 3 boxes chicken, 2 liters milk."
You MUST capture ALL items in the `items` array — never drop any item from the list.

## Daily Count Rule
Each day's inventory count is recorded separately. Only flag conflicts if two different workers counted the SAME item on the SAME day with different values.

## Storage Area
When no storage area is specified by the worker, use the Active Workspace storage area from context.
Always include the storage_area field in every item.

## PRICING RULE
For every item, include `unit_price` — an approximate US wholesale/restaurant-supply market price in USD per unit.
Base this on typical bulk restaurant supply pricing, not supermarket retail.
Examples: tomato $1.20/kg, chicken breast $5.50/kg, milk $1.10/liter, olive oil $6.00/liter, flour $0.60/kg, salmon $14.00/kg, cheddar $8.50/kg, rice $0.90/kg, cola cans $18.00/box.
If an item has been recorded before with a unit_price, use that same price unless the worker says it changed.
Always estimate — never leave unit_price null.

## Response Format (ALWAYS return valid JSON only — no markdown, no extra text)
{
  "message": "Your conversational response — be helpful, precise, and human",
  "action": "none | confirm | update | clarify | flag | query_result",
  "intent": "add | remove | set | query | expiry | analytics | confirm | deny | unknown",
  "data": {
    "items": [
      {
        "item_name": "string",
        "category": "string — choose from: Vegetables, Meat, Seafood, Dairy, Dry Goods, Beverages, Bakery, Frozen, Produce, Cleaning Supplies, or best fit",
        "quantity": number,
        "unit": "string",
        "storage_area": "string or null",
        "unit_price": number (approximate USD wholesale price per unit),
        "expiry_date": "YYYY-MM-DD or null",
        "operation": "add | subtract | set"
      }
    ],
    "confirmed": false,
    "flags": []
  }
}

## Flag values: unit_mismatch | suspicious_quantity | conflict | incomplete | expiry_warning | not_relevant

## Examples

User: "I have 5 kg tomatoes, 3 boxes chicken, 2 liters milk on Shelf A"
Response: {"message": "Got it — 5 kg tomatoes, 3 boxes chicken, 2 liters milk on Shelf A. Shall I add all of these?", "action": "confirm", "intent": "add", "data": {"items": [{"item_name": "tomato", "category": "Vegetables", "quantity": 5, "unit": "kg", "storage_area": "Shelf A", "unit_price": 1.20, "operation": "add"}, {"item_name": "chicken", "category": "Meat", "quantity": 3, "unit": "boxes", "storage_area": "Shelf A", "unit_price": 45.00, "operation": "add"}, {"item_name": "milk", "category": "Dairy", "quantity": 2, "unit": "liters", "storage_area": "Shelf A", "unit_price": 1.10, "operation": "add"}], "confirmed": false, "flags": []}}

User: "Yes" (after confirm)
Response: {"message": "Done! All items added to inventory.", "action": "update", "intent": "confirm", "data": {"items": [...same items...], "confirmed": true, "flags": []}}

User: "Remove milk" (missing quantity)
Response: {"message": "How many milk units should I remove?", "action": "clarify", "intent": "remove", "data": {"items": [{"item_name": "milk", "category": "Dairy", "quantity": null, "unit": null, "storage_area": null, "operation": "subtract"}], "confirmed": false, "flags": ["incomplete"]}}
"""


def _build_inventory_context(db: Session) -> str:
    today = date_type.today()
    items = (
        db.query(InventoryItem)
        .filter(InventoryItem.count_date == today)
        .order_by(InventoryItem.item_name)
        .limit(40)
        .all()
    )
    if not items:
        return f"No items counted yet today ({today}). Inventory is fresh for today."
    lines = [f"Today's inventory ({today}):"]
    for item in items:
        flag = " [FLAGGED]" if item.is_flagged else ""
        expiry = f", expires {item.expiry_date}" if item.expiry_date else ""
        loc = f"{item.storage_area}"
        if item.location_name:
            loc = f"{item.location_name} › {item.storage_area}"
        price = f", ~${item.unit_price:.2f}/{item.unit}" if item.unit_price else ""
        lines.append(
            f"  • {item.item_name} ({item.category}): {item.quantity} {item.unit}"
            f"{price} @ {loc}, counted by {item.updated_by}{expiry}{flag}"
        )
    return "\n".join(lines)


def _build_item_history_context(db: Session) -> str:
    """Most recent unit per item across ALL sessions — tells GPT-4o what unit each item was last tracked in."""
    subq = (
        db.query(
            InventoryItem.item_name,
            func.max(InventoryItem.timestamp).label("max_ts"),
        )
        .group_by(InventoryItem.item_name)
        .subquery()
    )
    items = (
        db.query(InventoryItem)
        .join(
            subq,
            (InventoryItem.item_name == subq.c.item_name)
            & (InventoryItem.timestamp == subq.c.max_ts),
        )
        .order_by(InventoryItem.item_name)
        .limit(80)
        .all()
    )
    if not items:
        return ""
    lines = ["Known item unit history (most recent record across ALL sessions):"]
    for item in items:
        loc = f"{item.location_name} › {item.storage_area}" if item.location_name else item.storage_area
        price = f", unit_price=${item.unit_price:.2f}" if item.unit_price else ""
        lines.append(
            f"  • {item.item_name}: {item.unit}{price} @ {loc} (last counted {item.count_date})"
        )
    return "\n".join(lines)


def _get_conversation_history(session_id: str, db: Session, limit: int = 20) -> list:
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


def _get_established_unit(item_name: str, db: Session) -> Optional[str]:
    existing = (
        db.query(InventoryItem)
        .filter(InventoryItem.item_name.ilike(f"%{item_name}%"))
        .order_by(InventoryItem.timestamp.desc())
        .first()
    )
    return existing.unit if existing else None


def _detect_unit_conflict(item_name: str, new_unit: str, db: Session) -> Optional[str]:
    established = _get_established_unit(item_name, db)
    if established and new_unit:
        eu = normalize_unit(established)
        iu = normalize_unit(new_unit)
        if eu != iu and not are_compatible_units(eu, iu):
            return established
    return None


def _detect_suspicious_quantity(item_name: str, quantity: float, db: Session) -> bool:
    today = date_type.today()
    existing = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.item_name.ilike(f"%{item_name}%"),
            InventoryItem.count_date == today,
        )
        .first()
    )
    if existing and existing.quantity > 0:
        return (quantity / existing.quantity) > 10
    return quantity > 500


def _detect_same_day_conflict(
    item_name: str, storage_area: str, worker_id: str, quantity: float, db: Session
) -> Optional[str]:
    today = date_type.today()
    other = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.item_name.ilike(f"%{item_name}%"),
            InventoryItem.storage_area == storage_area,
            InventoryItem.count_date == today,
            InventoryItem.updated_by != worker_id,
        )
        .first()
    )
    if other and abs(other.quantity - quantity) > 0.01:
        return f"{other.updated_by} counted {other.quantity} {other.unit} today"
    return None


def _is_non_food_item(item_name: str) -> bool:
    return any(kw in item_name.lower() for kw in NON_FOOD_KEYWORDS)


def _execute_single_item(item_data: dict, worker_id: str, db: Session, location_name: str = "") -> bool:
    item_name = item_data.get("item_name")
    quantity = item_data.get("quantity")
    unit = item_data.get("unit", "pieces")
    storage_area = item_data.get("storage_area") or "General Storage"
    operation = item_data.get("operation", "set")
    category = item_data.get("category") or "Unknown"
    expiry_date = item_data.get("expiry_date")
    unit_price = item_data.get("unit_price")
    today = date_type.today()

    if not item_name or quantity is None:
        return False

    existing = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.item_name.ilike(f"%{item_name}%"),
            InventoryItem.storage_area == storage_area,
            InventoryItem.count_date == today,
        )
        .first()
    )

    if existing:
        incoming_unit = normalize_unit(unit)
        stored_unit = normalize_unit(existing.unit)

        if incoming_unit != stored_unit and can_convert(stored_unit, incoming_unit):
            existing_in_new = convert(existing.quantity, existing.unit, unit)
            if operation == "add":
                existing.quantity = round(existing_in_new + quantity, 4)
            elif operation == "subtract":
                existing.quantity = max(0, round(existing_in_new - quantity, 4))
            else:
                existing.quantity = quantity
            existing.unit = incoming_unit
        else:
            if operation == "add":
                existing.quantity = round(existing.quantity + quantity, 4)
            elif operation == "subtract":
                existing.quantity = max(0, round(existing.quantity - quantity, 4))
            else:
                existing.quantity = quantity
                existing.unit = normalize_unit(unit)

        existing.updated_by = worker_id
        existing.timestamp = datetime.utcnow()
        if expiry_date:
            existing.expiry_date = expiry_date
        if category and category != "Unknown":
            existing.category = category
        if unit_price is not None:
            existing.unit_price = unit_price
    else:
        db.add(InventoryItem(
            item_name=item_name.lower().strip(),
            category=category,
            quantity=quantity,
            unit=normalize_unit(unit),
            storage_area=storage_area,
            location_name=location_name,
            unit_price=unit_price,
            expiry_date=expiry_date,
            updated_by=worker_id,
            count_date=today,
        ))

    op_label = {"add": "Added", "subtract": "Removed", "set": "Set"}.get(operation, "Updated")
    db.add(ActivityLog(
        action=op_label,
        item_name=item_name,
        details=f"{op_label} {quantity} {unit} of {item_name} at {storage_area} [{today}]",
        worker=worker_id,
    ))
    return True


def _execute_inventory_updates(data: dict, worker_id: str, db: Session, location_name: str = "") -> bool:
    items_list = _normalize_items(data)
    if not items_list:
        return False
    success = False
    for item_data in items_list:
        if _execute_single_item(item_data, worker_id, db, location_name=location_name):
            success = True
    if success:
        db.commit()
    return success


def _normalize_items(data: dict) -> list:
    """Accepts either data.items[] (new) or flat data.item_name (legacy)."""
    items = data.get("items")
    if items and isinstance(items, list):
        return [i for i in items if i.get("item_name") and i.get("quantity") is not None]
    if data.get("item_name") and data.get("quantity") is not None:
        return [{
            "item_name": data["item_name"],
            "category": data.get("category"),
            "quantity": data["quantity"],
            "unit": data.get("unit", "pieces"),
            "storage_area": data.get("storage_area"),
            "expiry_date": data.get("expiry_date"),
            "operation": data.get("operation", "add"),
        }]
    return []


GUARD_SYSTEM = """You are a restaurant inventory item validator. Your ONLY job: check whether items in the user's message are legitimate restaurant/hotel kitchen inventory items.

STEP 1 — Is this a message that contains inventory items?
- Set "has_items": false for: confirmations ("yes", "ok", "proceed", "add it"), queries ("what's in stock?"), greetings, or any message with no specific items. Return immediately with all_valid: true.

STEP 2 — For each item mentioned, classify:
A) CLEARLY VALID: recognized food, beverage, or kitchen/cleaning supply with no ambiguity → is_valid=true, is_ambiguous=false
B) AMBIGUOUS: the word has a primary non-food meaning OR is slang/regional that a system might misinterpret → is_ambiguous=true
   Examples of AMBIGUOUS: "rocket" (arugula OR spacecraft), "mars" (candy bar OR planet), "dove" (soap OR bird), "snickers" (candy OR laugh), "bounty" (chocolate OR paper towel)
   Even if you know the food meaning, flag it — the system needs human confirmation.
C) CLEARLY NON-FOOD: pen, paper, laptop, chair, phone, furniture, clothing → is_valid=false

STEP 3 — Build guard_message only if any item is ambiguous or invalid:
- Be specific: name the item and both possible interpretations
- Example: "Just to confirm — did you mean rocket the salad leaf (arugula), or something else?"
- For multiple issues: list them all in one message.

Return ONLY valid JSON, no markdown:
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
  "guard_message": "friendly clarification shown to worker — empty string if all_valid"
}"""


async def _guard_validate_items(text: str) -> dict:
    """Pre-validation LLM call that catches ambiguous or non-food items before ARIA processes them."""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": GUARD_SYSTEM},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=500,
            timeout=15.0,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[Guard] Failed: {e} — passing through to ARIA")
        return {"has_items": False, "all_valid": True, "items": [], "guard_message": ""}


async def process_message(
    text: str, session_id: str, worker_id: str, db: Session,
    storage_area: Optional[str] = None, location_name: Optional[str] = None
) -> dict:
    # ── Guard: validate items before ARIA processes them ──
    guard = await _guard_validate_items(text)
    if guard.get("has_items") and not guard.get("all_valid"):
        guard_message = guard.get("guard_message") or "Could you clarify what you mean by that item?"
        db.add(ConversationMessage(session_id=session_id, role="user", content=text))
        db.add(ConversationMessage(session_id=session_id, role="assistant", content=guard_message, action_taken="clarify"))
        db.commit()
        return {
            "message": guard_message,
            "action": "clarify",
            "data": {"items": guard.get("items", []), "confirmed": False, "flags": ["incomplete"], "guard": True},
            "inventory_updated": False,
            "session_id": session_id,
        }

    inventory_context = _build_inventory_context(db)
    item_history = _build_item_history_context(db)
    history = _get_conversation_history(session_id, db)

    workspace_ctx = ""
    if storage_area:
        workspace_ctx = (
            f"\n\n## Active Workspace\n"
            f"Location: {location_name or 'Unknown'}\n"
            f"Storage Area: {storage_area}\n"
            f"When the worker doesn't specify a storage area, use '{storage_area}' as the default."
        )

    history_ctx = f"\n\n## {item_history}" if item_history else ""

    system_with_context = (
        SYSTEM_PROMPT
        + f"\n\n## Live Inventory Context (today only)\n{inventory_context}"
        + history_ctx
        + workspace_ctx
        + f"\n\n## Worker ID: {worker_id}"
        + f"\n## Today's Date: {date_type.today()}"
    )

    messages = [{"role": "system", "content": system_with_context}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.3,
            response_format={"type": "json_object"},
            timeout=25.0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "message": "I had trouble processing that. Could you rephrase?",
            "action": "none",
            "intent": "unknown",
            "data": {"items": [], "confirmed": False, "flags": []},
        }
    except Exception as e:
        err_type = "timed out" if "timeout" in str(e).lower() or "timed out" in str(e).lower() else "failed"
        print(f"[ARIA] LLM call {err_type}: {e}")
        return {
            "message": f"The AI service {err_type} — please try again in a moment.",
            "action": "none",
            "data": {"items": [], "confirmed": False, "flags": []},
            "inventory_updated": False,
            "session_id": session_id,
        }

    action = parsed.get("action", "none")
    data = parsed.get("data", {})
    if "items" not in data:
        data["items"] = []
    inventory_updated = False

    items_list = _normalize_items(data)

    # ── Guard: non-food items ──
    non_food = [i["item_name"] for i in items_list if _is_non_food_item(i.get("item_name", ""))]
    if non_food:
        action = "none"
        parsed["action"] = "none"
        parsed["message"] = (
            f"I can only manage food, beverage, and kitchen supply inventory. "
            f"'{', '.join(non_food)}' {'don\'t' if len(non_food) > 1 else 'doesn\'t'} appear to be "
            f"food or kitchen items. Please check if you meant something else."
        )
        data.setdefault("flags", []).append("not_relevant")

    # ── Unit mismatch: flag only during non-update actions (not after execution) ──
    if action not in ("update", "none"):
        for item in items_list:
            if item.get("item_name") and item.get("unit"):
                conflict_unit = _detect_unit_conflict(item["item_name"], item["unit"], db)
                if conflict_unit:
                    data.setdefault("flags", []).append("unit_mismatch")
                    break

    # ── Suspicious quantity check ──
    if action == "confirm":
        for item in items_list:
            if item.get("item_name") and item.get("quantity"):
                if _detect_suspicious_quantity(item["item_name"], item["quantity"], db):
                    data.setdefault("flags", []).append("suspicious_quantity")
                    break

    # ── Same-day conflict check ──
    if action == "confirm":
        for item in items_list:
            if item.get("item_name") and item.get("quantity") and item.get("storage_area"):
                conflict_msg = _detect_same_day_conflict(
                    item["item_name"], item["storage_area"], worker_id, item["quantity"], db
                )
                if conflict_msg:
                    data.setdefault("flags", []).append("conflict")
                    parsed["message"] += f" ⚠ {conflict_msg}. Still confirm?"
                    break

    # Store pending when ARIA asks for confirmation
    if action == "confirm":
        _pending_actions[session_id] = data

    # Execute when confirmed
    if action == "update" and data.get("confirmed"):
        pending = _pending_actions.get(session_id, data)
        inventory_updated = _execute_inventory_updates(pending, worker_id, db, location_name=location_name or "")
        _pending_actions.pop(session_id, None)

    # Also handle "yes"/"ok" intent confirming a pending action
    if action == "none" and parsed.get("intent") == "confirm":
        pending = _pending_actions.get(session_id)
        if pending:
            inventory_updated = _execute_inventory_updates(pending, worker_id, db, location_name=location_name or "")
            _pending_actions.pop(session_id, None)

    db.add(ConversationMessage(session_id=session_id, role="user", content=text))
    db.add(ConversationMessage(
        session_id=session_id,
        role="assistant",
        content=parsed.get("message", ""),
        action_taken=action if action != "none" else None,
    ))
    db.commit()

    return {
        "message": parsed.get("message", ""),
        "action": action,
        "data": data,
        "inventory_updated": inventory_updated,
        "session_id": session_id,
    }
