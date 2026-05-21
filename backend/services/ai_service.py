import asyncio
import json
import os
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import ConversationMessage, ActivityLog, InventoryItem, UserProfile, UserLexicon
from utils.unit_converter import normalize_unit, can_convert, convert, are_compatible_units, extract_fuzzy_units
from datetime import datetime, date as date_type

from crew.crew import guard_validate, aria_process

_pending_actions: dict = {}


def clear_session_pending(session_id: str) -> None:
    _pending_actions.pop(session_id, None)

import re as _re


def _build_completion_hint(current_text: str, history: list) -> str:
    """
    Detect fragment-completion patterns across turns and return an explicit instruction
    for ARIA so it doesn't ask for information the worker already gave.

    Patterns:
      A) ARIA asked "what item?" after worker gave qty+unit → worker now says the item name
      B) ARIA asked "quantity/unit?" after worker gave item → worker now gives qty/unit
    """
    if len(history) < 2:
        return ""

    last_asst = None
    prev_user = None
    for msg in reversed(history):
        if msg["role"] == "assistant" and last_asst is None:
            last_asst = msg["content"]
        elif msg["role"] == "user" and last_asst is not None and prev_user is None:
            prev_user = msg["content"]
            break

    if not last_asst or not prev_user:
        return ""

    asst_lower = last_asst.lower()
    asked_item = any(kw in asst_lower for kw in (
        "what item", "which item", "tell me what", "item to add",
        "specify the item", "item you're referring", "name of the item",
        "can you tell me what", "what are you",
    ))
    asked_qty = any(kw in asst_lower for kw in (
        "quantity", "how much", "how many", "what unit", "specify the quantity",
        "how many", "unit you want",
    ))

    if not (asked_item or asked_qty):
        return ""

    curr = current_text.strip()
    prev = prev_user.strip()

    if asked_item:
        m = _re.search(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?', prev)
        if m:
            qty = m.group(1)
            raw_unit = (m.group(2) or "").strip()
            unit = normalize_unit(raw_unit) if raw_unit else "pieces"
            return (
                f"\n⚑ FRAGMENT COMPLETION: Worker's earlier message '{prev}' = qty {qty}, unit {unit}. "
                f"ARIA asked for the item. Current reply '{curr}' IS the item. "
                f"Combine → {qty} {unit} of {curr}. Go to action='confirm'. "
                f"Do NOT ask for quantity or unit — they were already given."
            )

    if asked_qty:
        m = _re.search(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?', curr)
        if m:
            qty = m.group(1)
            raw_unit = (m.group(2) or "").strip()
            unit = normalize_unit(raw_unit) if raw_unit else "pieces"
            return (
                f"\n⚑ FRAGMENT COMPLETION: Worker's earlier message '{prev}' is the item. "
                f"ARIA asked for quantity/unit. Current reply '{curr}' = qty {qty}, unit {unit}. "
                f"Combine → {qty} {unit} of {prev}. Go to action='confirm'. "
                f"Do NOT ask for the item — it was already given."
            )

    return ""


_AFFIRMATION_CORE = {
    "yes", "yep", "yeah", "yup", "ya", "yah", "sure", "ok", "okay",
    "fine", "go", "confirm", "confirmed", "proceed", "correct", "right",
    "absolutely", "definitely", "of course", "for sure", "alright",
    "sounds good", "perfect", "great", "good", "do it", "add it",
    "go ahead", "let's go", "let go", "exactly", "indeed",
}
_AFFIRMATION_STARTERS = {"yes", "yep", "yeah", "yup", "ya", "sure", "ok", "okay", "fine"}
_LAUGH_WORDS = {"haha", "hehe", "lol", "lmao", "😂", "😄"}
_ACTION_WORDS = {"add", "go", "do", "confirm", "proceed", "put"}


def _is_affirmation(text: str) -> bool:
    """Detect informal/elongated confirmations like 'yeppp', 'yess add', 'haha fine add it'."""
    t = text.lower().strip().rstrip(".,!?")
    # Normalize elongated characters: yeppp→yep, yesss→yes, okkkk→ok
    normalized = _re.sub(r'(.)\1{1,}', r'\1', t)
    words = normalized.split()
    if not words:
        return False
    # Exact match after normalization
    if normalized in _AFFIRMATION_CORE:
        return True
    # First word is a clear affirmation
    if words[0] in _AFFIRMATION_STARTERS:
        return True
    # "haha fine", "haha ok", "haha yes", "lol sure"
    if words[0] in _LAUGH_WORDS and len(words) > 1:
        rest = " ".join(words[1:])
        rest_norm = _re.sub(r'(.)\1{1,}', r'\1', rest)
        if rest_norm.split()[0] in (_AFFIRMATION_STARTERS | _ACTION_WORDS | {"fine"}):
            return True
    # Contains an action verb after an affirmation (e.g. "fine add it", "ok go ahead")
    if len(words) >= 2 and words[0] in (_AFFIRMATION_STARTERS | {"fine"}) and words[1] in _ACTION_WORDS:
        return True
    return False


NON_FOOD_KEYWORDS = {
    "paper", "pen", "pencil", "stapler", "printer", "laptop", "computer", "phone",
    "mobile", "tablet", "chair", "table", "desk", "furniture", "cloth", "shirt",
    "shoes", "bag", "book", "notebook", "scissors", "tape", "glue", "paint",
    "battery", "cable", "charger", "keyboard", "mouse", "monitor", "tv", "remote",
}


# ──────────────────────────────────────────────
# Context builders
# ──────────────────────────────────────────────

def _build_inventory_context(
    db: Session,
    storage_area: str = "",
    location_name: str = "",
) -> str:
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

    # Pre-compute grand total across all locations
    grand_total = sum(
        round(i.quantity * i.unit_price, 2)
        for i in items
        if i.quantity and i.unit_price
    )

    lines = [
        f"Today's inventory ({today}):",
        f"  [Grand total across ALL locations: ${round(grand_total, 2):.2f} — use this exact figure when asked about all-location totals]",
    ]
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

    # Workspace-specific pre-computed summary — prevents LLM arithmetic errors
    if storage_area:
        workspace_items = [
            i for i in items
            if i.storage_area == storage_area
            and (not location_name or i.location_name == location_name)
        ]
        if workspace_items:
            workspace_label = f"{location_name} › {storage_area}" if location_name else storage_area
            total_value = sum(
                round(i.quantity * i.unit_price, 2)
                for i in workspace_items
                if i.quantity and i.unit_price
            )
            lines.append(f"\n## Pre-computed workspace summary for '{workspace_label}'")
            lines.append(f"  Item count: {len(workspace_items)}")
            lines.append(f"  Total value: ${round(total_value, 2):.2f}")
            lines.append("  Items:")
            for i in workspace_items:
                item_total = round(i.quantity * i.unit_price, 2) if i.quantity and i.unit_price else 0.0
                price_str = (
                    f"{i.quantity} {i.unit} × ${i.unit_price:.2f} = ${item_total:.2f}"
                    if i.unit_price else f"{i.quantity} {i.unit} (no price)"
                )
                lines.append(f"    - {i.item_name}: {price_str}")
            lines.append(
                f"  IMPORTANT: When asked about total value for '{workspace_label}', "
                f"always report ${round(total_value, 2):.2f} — do NOT recompute."
            )

    return "\n".join(lines)


def _build_item_history_context(db: Session) -> str:
    """Most recent unit per item across ALL sessions."""
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


def _get_conversation_history(session_id: str, db: Session, limit: int = 30) -> list:
    """Full session conversation history — increased limit for better context."""
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(messages)]


def _build_user_profile_context(worker_id: str, db: Session) -> str:
    """Build user profile context for ARIA — emotion, tone, known lexicons."""
    profile = db.query(UserProfile).filter(UserProfile.worker_id == worker_id).first()
    lexicons = db.query(UserLexicon).filter(UserLexicon.worker_id == worker_id).order_by(
        UserLexicon.usage_count.desc()
    ).limit(30).all()

    lines = []
    if profile:
        lines.append(f"Worker emotion state: {profile.emotion_state}")
        lines.append(f"Tone preference: {profile.tone_preference}")
        if profile.personality_notes:
            lines.append(f"Personality notes: {profile.personality_notes}")
    else:
        lines.append("Worker emotion state: neutral")
        lines.append("Tone preference: friendly_fun")

    if lexicons:
        lines.append("Known lexicons (use these to auto-resolve the worker's personal vocabulary):")
        for lex in lexicons:
            resolved = f" → {lex.resolved_word}" if lex.resolved_word else ""
            lines.append(f"  • '{lex.original_word}'{resolved} ({lex.word_type}, used {lex.usage_count}x)")
    else:
        lines.append("No custom lexicons recorded yet.")

    return "\n".join(lines)


def _get_or_create_user_profile(worker_id: str, db: Session) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.worker_id == worker_id).first()
    if not profile:
        profile = UserProfile(worker_id=worker_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _save_user_insights(worker_id: str, aria_result: dict, db: Session) -> None:
    """Persist emotion state, tone preference, new lexicons, and personality note."""
    user_emotion = aria_result.get("user_emotion", "neutral")
    personality_note = aria_result.get("personality_note")
    new_lexicons = aria_result.get("new_lexicons", [])

    profile = _get_or_create_user_profile(worker_id, db)

    # One-time cleanup: deduplicate any existing near-identical observations already stored
    if profile.personality_notes:
        raw = [o.strip() for o in profile.personality_notes.split("|") if o.strip()]
        deduped = []
        for obs in raw:
            obs_words = set(obs.lower().split())
            already = any(
                len(obs_words & set(d.lower().split())) / max(len(obs_words | set(d.lower().split())), 1) > 0.60
                for d in deduped
            )
            if not already:
                deduped.append(obs)
        if len(deduped) != len(raw):
            profile.personality_notes = " | ".join(deduped[:3])

    # Update emotion and tone preference
    profile.emotion_state = user_emotion
    if user_emotion in ("frustrated", "angry"):
        profile.tone_preference = "formal"
    elif user_emotion in ("happy", "neutral"):
        profile.tone_preference = "friendly_fun"

    # Update personality note — replace when semantically similar, add when genuinely new
    if personality_note:
        existing = profile.personality_notes or ""
        observations = [o.strip() for o in existing.split("|") if o.strip()]
        new_words = set(personality_note.lower().split())

        # Find if any existing observation is >60% word-overlap (near-duplicate)
        match_idx = None
        for i, obs in enumerate(observations):
            obs_words = set(obs.lower().split())
            union = obs_words | new_words
            overlap = len(obs_words & new_words) / max(len(union), 1)
            if overlap > 0.60:
                match_idx = i
                break

        if match_idx is not None:
            observations[match_idx] = personality_note   # replace near-duplicate in place
        else:
            observations.insert(0, personality_note)     # genuinely new insight

        profile.personality_notes = " | ".join(observations[:3])

    profile.updated_at = datetime.utcnow()

    # Upsert lexicons
    for entry in new_lexicons:
        original = entry.get("original_word", "").strip().lower()
        resolved = entry.get("resolved_word", "").strip().lower()
        word_type = entry.get("word_type", "unknown")
        if not original:
            continue
        existing_lex = db.query(UserLexicon).filter(
            UserLexicon.worker_id == worker_id,
            UserLexicon.original_word == original,
        ).first()
        if existing_lex:
            existing_lex.usage_count += 1
            existing_lex.last_seen = datetime.utcnow()
            if resolved:
                existing_lex.resolved_word = resolved
        else:
            db.add(UserLexicon(
                worker_id=worker_id,
                original_word=original,
                resolved_word=resolved or None,
                word_type=word_type,
            ))

    db.commit()


# ──────────────────────────────────────────────
# Validation helpers
# ──────────────────────────────────────────────

def _get_established_unit(item_name: str, db: Session) -> Optional[str]:
    existing = (
        db.query(InventoryItem)
        .filter(InventoryItem.item_name.ilike(f"%{item_name}%"))
        .order_by(InventoryItem.timestamp.desc())
        .first()
    )
    return existing.unit if existing else None


# ──────────────────────────────────────────────
# Storage appropriateness check (code-level, not prompt-only)
# ──────────────────────────────────────────────

_COLD_STORAGE_NAMES = {"cold storage", "chiller", "refrigerator", "fridge", "cold room", "cool room", "walk-in"}
_FREEZER_NAMES = {"freezer", "frozen storage", "deep freeze", "blast freezer", "blast chiller"}
_DRY_STORAGE_NAMES = {"dry storage", "dry store", "dry goods", "pantry", "shelf", "rack storage", "storeroom"}
_BAR_NAMES = {"bar", "cellar", "wine cellar", "spirits room"}

_DRY_ITEM_KEYWORDS = {
    "rice", "flour", "sugar", "pasta", "noodle", "bean", "lentil", "grain",
    "cereal", "oat", "barley", "wheat", "quinoa", "couscous", "oil", "vinegar",
    "salt", "spice", "herb", "canned", "dried", "biscuit", "cracker", "chip",
    "tea", "coffee", "powder",
}
_FRESH_PROTEIN_KEYWORDS = {
    "meat", "chicken", "beef", "pork", "lamb", "mutton", "veal", "duck",
    "fish", "seafood", "prawn", "shrimp", "salmon", "tuna", "crab", "lobster", "squid",
}
_DAIRY_KEYWORDS = {"milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "egg", "tofu"}
_FROZEN_KEYWORDS = {"ice cream", "gelato", "sorbet", "frozen"}
_TROPICAL_KEYWORDS = {"coconut", "banana", "mango", "avocado", "papaya", "guava", "durian"}


def _classify_storage_type(storage_area: str) -> Optional[str]:
    """Return 'cold' | 'freezer' | 'dry' | 'bar' | None. None = generic/unknown — skip checks."""
    name = storage_area.lower().strip()
    if any(k in name for k in _FREEZER_NAMES):
        return "freezer"
    if any(k in name for k in _COLD_STORAGE_NAMES):
        return "cold"
    if any(k in name for k in _DRY_STORAGE_NAMES):
        return "dry"
    if name in _BAR_NAMES or name.startswith("bar ") or name.endswith(" bar"):
        return "bar"
    return None


def _check_storage_appropriateness(item_name: str, category: str, storage_area: str) -> Optional[str]:
    """Return a warning string if item is unusual for storage_area, else None."""
    storage_type = _classify_storage_type(storage_area)
    if not storage_type:
        return None

    name = item_name.lower()
    cat = category.lower()
    item_title = item_name.title()

    is_dry = any(k in name for k in _DRY_ITEM_KEYWORDS) or cat in ("dry goods",)
    is_protein = any(k in name for k in _FRESH_PROTEIN_KEYWORDS) or cat in ("meat", "seafood")
    is_dairy = any(k in name for k in _DAIRY_KEYWORDS) or cat in ("dairy",)
    is_frozen = any(k in name for k in _FROZEN_KEYWORDS) or cat in ("frozen",)
    is_tropical = any(k in name for k in _TROPICAL_KEYWORDS)

    if storage_type == "cold":
        if is_dry:
            return (
                f"⚠ {item_title} is a dry good — usually stored in dry storage, not cold storage. "
                f"Are you sure you want to store it in {storage_area}?"
            )
        if is_tropical:
            return (
                f"⚠ {item_title} is a tropical item best kept at room temperature, not refrigerated. "
                f"Sure about {storage_area}?"
            )

    elif storage_type == "freezer":
        if is_dry:
            return (
                f"⚠ {item_title} doesn't need freezing — it's a dry good. "
                f"Sure you want to store it in {storage_area}?"
            )
        if is_dairy and "ice cream" not in name and "gelato" not in name:
            return (
                f"⚠ Freezing {item_title} will solidify it. "
                f"Is {storage_area} the right place, or did you mean cold storage?"
            )

    elif storage_type == "dry":
        if is_protein:
            return (
                f"⚠ {item_title} needs refrigeration, not dry storage. "
                f"Sure you want to store it in {storage_area}?"
            )
        if is_dairy:
            return (
                f"⚠ {item_title} needs refrigeration, not dry storage. "
                f"Sure about {storage_area}?"
            )
        if is_frozen:
            return (
                f"⚠ {item_title} needs to be frozen, not kept in dry storage. "
                f"Sure about {storage_area}?"
            )

    return None


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


# ──────────────────────────────────────────────
# Inventory execution — Fix #3: workspace storage always wins
# ──────────────────────────────────────────────

def _execute_single_item(
    item_data: dict,
    worker_id: str,
    db: Session,
    workspace_location: str = "",
    workspace_storage: str = "",
) -> bool:
    item_name = item_data.get("item_name")
    quantity = item_data.get("quantity")
    unit = item_data.get("unit", "pieces")
    # Fix #3: workspace values always override whatever AI returned
    storage_area = workspace_storage or item_data.get("storage_area") or "General Storage"
    location_name = workspace_location or item_data.get("location_name", "")
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


def _execute_inventory_updates(
    data: dict,
    worker_id: str,
    db: Session,
    workspace_location: str = "",
    workspace_storage: str = "",
) -> bool:
    items_list = _normalize_items(data)
    if not items_list:
        return False
    success = False
    for item_data in items_list:
        if _execute_single_item(
            item_data, worker_id, db,
            workspace_location=workspace_location,
            workspace_storage=workspace_storage,
        ):
            success = True
    if success:
        db.commit()
    return success


def _normalize_items(data: dict) -> list:
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


# ──────────────────────────────────────────────
# Guard + ARIA crew wrappers
# ──────────────────────────────────────────────

async def _guard_validate_items(text: str) -> dict:
    return await asyncio.to_thread(guard_validate, text)


# ──────────────────────────────────────────────
# Main processing function
# ──────────────────────────────────────────────

async def process_message(
    text: str,
    session_id: str,
    worker_id: str,
    db: Session,
    storage_area: Optional[str] = None,
    location_name: Optional[str] = None,
) -> dict:
    # ── Eagerly ensure user profile exists — regardless of what happens next ──
    try:
        _get_or_create_user_profile(worker_id, db)
    except Exception as e:
        print(f"[UserProfile] Could not create profile for {worker_id}: {e}")

    # ── Fix #2: Detect fuzzy units before Guard so Guard doesn't misclassify them ──
    fuzzy_hits = extract_fuzzy_units(text)
    fuzzy_units_hint = ""
    if fuzzy_hits:
        pairs = ", ".join(f"'{orig}' → '{corr}'" for orig, corr in fuzzy_hits)
        fuzzy_units_hint = (
            f"Fuzzy unit typos detected in this message: {pairs}. "
            f"Suggest the correction to the worker (action='clarify') and use the corrected unit."
        )

    # ── Guard: validate items ──
    # Skip Guard for very short messages — they are almost always corrections, confirmations,
    # or unit completions. Let ARIA resolve them from conversation history instead.
    word_count = len(text.split())
    last_history = _get_conversation_history(session_id, db, limit=1)
    last_was_clarify = (
        last_history and
        last_history[-1].get("role") == "assistant" and
        any(kw in last_history[-1].get("content", "").lower()
            for kw in ("did you mean", "could you clarify", "please confirm", "please specify",
                       "unrecognized", "referring to", "what item", "which item",
                       "tell me what", "item to add", "quantity", "how much", "how many",
                       "what unit", "can you tell me"))
    )
    skip_guard = word_count <= 3 or last_was_clarify

    guard = {} if skip_guard else await _guard_validate_items(text)
    if guard.get("has_items") and not guard.get("all_valid"):
        guard_message = guard.get("guard_message") or "Could you clarify what you mean by that item?"
        db.add(ConversationMessage(session_id=session_id, role="user", content=text))
        db.add(ConversationMessage(
            session_id=session_id, role="assistant",
            content=guard_message, action_taken="clarify",
        ))
        db.commit()
        return {
            "message": guard_message,
            "action": "clarify",
            "data": {"items": guard.get("items", []), "confirmed": False, "flags": ["incomplete"], "guard": True},
            "inventory_updated": False,
            "session_id": session_id,
        }

    # ── Build all context ──
    inventory_context = _build_inventory_context(
        db,
        storage_area=storage_area or "",
        location_name=location_name or "",
    )
    item_history_context = _build_item_history_context(db)
    history = _get_conversation_history(session_id, db, limit=30)
    conversation_history_json = json.dumps(history, indent=2)
    completion_hint = _build_completion_hint(text, history)
    user_profile_context = _build_user_profile_context(worker_id, db)

    workspace_storage = storage_area or "General Storage"
    workspace_context = (
        f"Location: {location_name or 'Unknown'}\n"
        f"Storage Area: {workspace_storage}\n"
        f"IMPORTANT: These values are FIXED by the UI. Always use '{workspace_storage}' as storage_area "
        f"in every item, regardless of what the worker says in speech."
    ) if storage_area else (
        f"No specific workspace set. Use 'General Storage' as default storage_area."
    )

    pending = _pending_actions.get(session_id)
    affirmation_detected = _is_affirmation(text) and pending is not None

    if affirmation_detected:
        # Hard override: worker clearly said yes — force ARIA to execute, no more asking
        pending_action_context = (
            f"⚡ AFFIRMATION DETECTED — worker said '{text}' which means YES/CONFIRMED.\n"
            f"Execute this NOW: action='update', confirmed=true.\n"
            f"Pending:\n{json.dumps(pending, indent=2)}"
        )
    elif pending:
        pending_action_context = (
            f"Pending confirmation for this session:\n{json.dumps(pending, indent=2)}\n"
            "If the worker says yes/confirm/proceed/that's correct, execute this (action='update', confirmed=true)."
        )
    else:
        pending_action_context = "No pending action."

    if completion_hint:
        pending_action_context = completion_hint + "\n\n" + pending_action_context

    # ── Run ARIA crew ──
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
            user_profile_context=user_profile_context,
            fuzzy_units_hint=fuzzy_units_hint,
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

    # ── Unit mismatch flag (for non-update actions) ──
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

    # ── Storage appropriateness check ──
    if action == "confirm":
        for item in items_list:
            warning = _check_storage_appropriateness(
                item.get("item_name", ""),
                item.get("category", ""),
                workspace_storage,   # always check against the REAL workspace, not AI's suggestion
            )
            if warning:
                data.setdefault("flags", []).append("storage_warning")
                parsed["message"] = parsed.get("message", "") + f"\n{warning}"
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

    # ── Store pending — override storage_area with real workspace so confirm card is accurate ──
    if action == "confirm":
        if workspace_storage:
            for item in data.get("items", []):
                if isinstance(item, dict):
                    item["storage_area"] = workspace_storage
        _pending_actions[session_id] = data

    # ── Execute when confirmed ──
    if action == "update" and data.get("confirmed"):
        pending = _pending_actions.get(session_id, data)
        inventory_updated = _execute_inventory_updates(
            pending, worker_id, db,
            workspace_location=location_name or "",
            workspace_storage=storage_area or "",
        )
        _pending_actions.pop(session_id, None)

    # ── Handle "yes"/"ok" intent confirming a pending action ──
    if action == "none" and parsed.get("intent") == "confirm":
        pending = _pending_actions.get(session_id)
        if pending:
            inventory_updated = _execute_inventory_updates(
                pending, worker_id, db,
                workspace_location=location_name or "",
                workspace_storage=storage_area or "",
            )
            _pending_actions.pop(session_id, None)

    # ── Python-level fallback: affirmation detected but ARIA still didn't execute ──
    if affirmation_detected and not inventory_updated and session_id in _pending_actions:
        pending = _pending_actions.pop(session_id)
        inventory_updated = _execute_inventory_updates(
            pending, worker_id, db,
            workspace_location=location_name or "",
            workspace_storage=storage_area or "",
        )
        if inventory_updated and parsed.get("action") not in ("update",):
            parsed["message"] = f"Done! Added to {storage_area or 'storage'}."
            action = "update"

    # ── Clear pending on denial intent (belt-and-suspenders) ──
    if parsed.get("intent") == "deny":
        _pending_actions.pop(session_id, None)

    # ── Save conversation ──
    db.add(ConversationMessage(session_id=session_id, role="user", content=text))
    db.add(ConversationMessage(
        session_id=session_id,
        role="assistant",
        content=parsed.get("message", ""),
        action_taken=action if action != "none" else None,
    ))
    db.commit()

    # ── Save user insights (emotion, lexicons, personality) ──
    try:
        _save_user_insights(worker_id, parsed, db)
    except Exception as e:
        import traceback
        print(f"[UserInsights] Failed to save for {worker_id}: {type(e).__name__}: {e}")
        traceback.print_exc()

    # Deduplicate flags (ARIA + Python checks can both append the same flag)
    if "flags" in data:
        data["flags"] = list(dict.fromkeys(data["flags"]))

    return {
        "message": parsed.get("message", ""),
        "action": action,
        "data": data,
        "inventory_updated": inventory_updated,
        "session_id": session_id,
    }
