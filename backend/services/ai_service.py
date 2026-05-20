import asyncio
import json
import os
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import ConversationMessage, ActivityLog, InventoryItem
from utils.unit_converter import normalize_unit, can_convert, convert, are_compatible_units
from datetime import datetime, date as date_type

from crew.crew import guard_validate, aria_process

_pending_actions: dict = {}

NON_FOOD_KEYWORDS = {
    "paper", "pen", "pencil", "stapler", "printer", "laptop", "computer", "phone",
    "mobile", "tablet", "chair", "table", "desk", "furniture", "cloth", "shirt",
    "shoes", "bag", "book", "notebook", "scissors", "tape", "glue", "paint",
    "battery", "cable", "charger", "keyboard", "mouse", "monitor", "tv", "remote",
}


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


async def _guard_validate_items(text: str) -> dict:
    """Guard validation via CrewAI — ignores transcription noise, flags only real invalid items."""
    return await asyncio.to_thread(guard_validate, text)


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
    item_history_context = _build_item_history_context(db)
    history = _get_conversation_history(session_id, db)
    conversation_history_json = json.dumps(history, indent=2)

    workspace_context = (
        f"Location: {location_name or 'Unknown'}\n"
        f"Storage Area: {storage_area}\n"
        f"When the worker doesn't specify a storage area, use '{storage_area}' as the default."
    ) if storage_area else "No specific workspace set. Use 'General Storage' as default."

    pending = _pending_actions.get(session_id)
    pending_action_context = (
        f"Pending confirmation for this session:\n{json.dumps(pending, indent=2)}\n"
        "If the worker says yes/confirm/proceed/that's correct, execute this (action='update', confirmed=true)."
    ) if pending else "No pending action."

    try:
        parsed = await asyncio.to_thread(
            aria_process,
            text,
            inventory_context,
            item_history_context,
            conversation_history_json,
            workspace_context,
            pending_action_context,
            worker_id,
            str(date_type.today()),
        )
    except Exception as e:
        err_type = "timed out" if "timeout" in str(e).lower() or "timed out" in str(e).lower() else "failed"
        print(f"[ARIA Crew] {err_type}: {e}")
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
