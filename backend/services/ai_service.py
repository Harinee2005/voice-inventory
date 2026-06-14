import asyncio
import json
import logging
import os
from typing import Optional, AsyncGenerator, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from models import ConversationMessage, ActivityLog, InventoryItem, UserProfile, UserLexicon, PendingAction, RejectedItem
from utils.unit_converter import normalize_unit, can_convert, convert, are_compatible_units, extract_fuzzy_units
from datetime import datetime, date as date_type, timedelta
from logging_config import J

logger = logging.getLogger(__name__)

_PENDING_TTL_MINUTES = int(os.getenv("PENDING_TTL_MINUTES", "15"))
_SESSION_EXPIRY_HOURS = int(os.getenv("SESSION_EXPIRY_HOURS", "4"))


# ── DB-backed pending action store ────────────────────────────────────────────

def get_pending_action(session_id: str, db: Session) -> dict | None:
    row = db.query(PendingAction).filter(PendingAction.session_id == session_id).first()
    if not row:
        return None
    if row.expires_at < datetime.utcnow():
        db.delete(row)
        db.commit()
        logger.info("PENDING EXPIRED  session=%s", session_id)
        return None
    return json.loads(row.payload)


def set_pending_action(session_id: str, data: dict, db: Session) -> None:
    payload = json.dumps(data)
    expires = datetime.utcnow() + timedelta(minutes=_PENDING_TTL_MINUTES)
    row = db.query(PendingAction).filter(PendingAction.session_id == session_id).first()
    if row:
        row.payload = payload
        row.expires_at = expires
        row.created_at = datetime.utcnow()
    else:
        db.add(PendingAction(session_id=session_id, payload=payload, expires_at=expires))
    db.commit()
    logger.info("PENDING SET  session=%s  expires=%s", session_id, expires.isoformat())


def clear_pending_action(session_id: str, db: Session) -> None:
    deleted = db.query(PendingAction).filter(PendingAction.session_id == session_id).delete()
    db.commit()
    if deleted:
        logger.info("PENDING CLEARED  session=%s", session_id)


def clear_session_pending(session_id: str, db: Session | None = None) -> None:
    """Clear pending action for a session. Accepts optional db for router callers."""
    if db is not None:
        clear_pending_action(session_id, db)
    else:
        logger.warning("clear_session_pending called without db — no-op (DB-backed store requires db)")


# ── Guard rejection log ───────────────────────────────────────────────────────

def save_guard_rejections(session_id: str, guard_result: dict, db: Session) -> None:
    """Persist rejected/ambiguous items so ARIA doesn't re-suggest them this session."""
    items = guard_result.get("items", [])
    for item in items:
        if not item.get("is_valid") or item.get("is_ambiguous"):
            db.add(RejectedItem(
                session_id=session_id,
                item_name=item.get("name", ""),
                reason=item.get("concern") or ("ambiguous" if item.get("is_ambiguous") else "non-food"),
            ))
    if items:
        db.commit()


def load_recent_rejections(session_id: str, db: Session, limit: int = 3) -> str:
    """Return a context string listing recently rejected items for this session."""
    rows = (
        db.query(RejectedItem)
        .filter(RejectedItem.session_id == session_id)
        .order_by(RejectedItem.timestamp.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return ""
    lines = [f"• {r.item_name} ({r.reason})" for r in rows]
    return "## Items rejected this session (do NOT re-suggest)\n" + "\n".join(lines)

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
    # Office / stationery
    "paper", "pen", "pencil", "stapler", "printer", "scissors", "tape", "glue",
    "book", "notebook",
    # Electronics
    "laptop", "computer", "phone", "mobile", "tablet", "keyboard", "mouse",
    "monitor", "tv", "television", "remote", "battery", "cable", "charger",
    "camera", "headphone", "speaker",
    # Furniture / fixtures
    "chair", "table", "desk", "furniture", "sofa", "couch", "shelf", "rack",
    # Clothing / personal items
    "cloth", "shirt", "shoes", "pants", "dress", "jacket", "uniform", "bag",
    # Vehicles / machinery
    "bike", "bicycle", "motorbike", "motorcycle", "car", "truck", "van",
    "vehicle", "engine", "motor", "machine", "tractor", "scooter",
    # Tools / hardware
    "hammer", "drill", "wrench", "screwdriver", "nail", "bolt", "wire", "pipe",
    "paint", "brush",
    # Miscellaneous non-inventory
    "money", "cash", "coin", "invoice", "receipt",
}

# Generic category terms that are too vague to be a valid inventory item.
# Worker must specify the actual item (e.g. "tomato" not "vegetable").
GENERIC_CATEGORY_TERMS = {
    "vegetable", "vegetables",
    "fruit", "fruits",
    "meat", "meats",
    "seafood", "seafoods",
    "dairy",
    "grain", "grains",
    "beverage", "beverages",
    "produce",
    "food", "foods",
    "ingredient", "ingredients",
    "item", "items",
    "stuff", "things",
    "grocery", "groceries",
    "supply", "supplies",
    "spice", "spices",
    "herb", "herbs",
}

# Example hints shown per category to help the worker be specific
_GENERIC_EXAMPLES = {
    "vegetable": "tomato, carrot, spinach",
    "vegetables": "tomato, carrot, spinach",
    "fruit": "apple, mango, banana",
    "fruits": "apple, mango, banana",
    "meat": "chicken, beef, pork",
    "meats": "chicken, beef, pork",
    "seafood": "salmon, prawn, tuna",
    "dairy": "milk, paneer, curd",
    "grain": "rice, wheat flour, oats",
    "grains": "rice, wheat flour, oats",
    "beverage": "coke, orange juice, water",
    "beverages": "coke, orange juice, water",
    "spice": "turmeric, chilli powder, pepper",
    "spices": "turmeric, chilli powder, pepper",
}


def _is_generic_category(item_name: str) -> bool:
    """Return True if item_name is a vague category term, not a specific inventory item."""
    words = set(item_name.lower().split())
    return bool(words & GENERIC_CATEGORY_TERMS)


def _generic_category_hint(item_name: str) -> str:
    """Return an example hint for the detected generic term."""
    for word in item_name.lower().split():
        if word in _GENERIC_EXAMPLES:
            return _GENERIC_EXAMPLES[word]
    return "a specific item name"


# ──────────────────────────────────────────────
# Context builders
# ──────────────────────────────────────────────

def _build_inventory_context(
    db: Session,
    storage_area: str = "",
    location_name: str = "",
) -> str:
    today = date_type.today()
    rows = db.execute(
        text("SELECT * FROM v_today_inventory WHERE count_date = :today ORDER BY item_name LIMIT 40"),
        {"today": str(today)},
    ).mappings().all()

    if not rows:
        return f"No items counted yet today ({today}). Inventory is fresh for today."

    grand_total = sum(
        round(r["quantity"] * r["unit_price"], 2)
        for r in rows
        if r["quantity"] and r["unit_price"]
    )

    lines = [
        f"Today's inventory ({today}) [source: v_today_inventory]:",
        f"  [Grand total across ALL locations: ${round(grand_total, 2):.2f} — use this exact figure when asked about all-location totals]",
    ]
    for r in rows:
        flag = " [FLAGGED]" if r["is_flagged"] else ""
        expiry = f", expires {r['expiry_date']}" if r["expiry_date"] else ""
        loc = f"{r['storage_area']}"
        if r["location_name"]:
            loc = f"{r['location_name']} › {r['storage_area']}"
        price = f", ~${r['unit_price']:.2f}/{r['unit']}" if r["unit_price"] else ""
        lines.append(
            f"  • {r['item_name']} ({r['category']}): {r['quantity']} {r['unit']}"
            f"{price} @ {loc}, counted by {r['updated_by']}{expiry}{flag}"
        )

    if storage_area:
        workspace_rows = [
            r for r in rows
            if r["storage_area"] == storage_area
            and (not location_name or r["location_name"] == location_name)
        ]
        if workspace_rows:
            workspace_label = f"{location_name} › {storage_area}" if location_name else storage_area
            total_value = sum(
                round(r["quantity"] * r["unit_price"], 2)
                for r in workspace_rows
                if r["quantity"] and r["unit_price"]
            )
            lines.append(f"\n## Pre-computed workspace summary for '{workspace_label}'")
            lines.append(f"  Item count: {len(workspace_rows)}")
            lines.append(f"  Total value: ${round(total_value, 2):.2f}")
            lines.append("  Items:")
            for r in workspace_rows:
                item_total = round(r["quantity"] * r["unit_price"], 2) if r["quantity"] and r["unit_price"] else 0.0
                price_str = (
                    f"{r['quantity']} {r['unit']} × ${r['unit_price']:.2f} = ${item_total:.2f}"
                    if r["unit_price"] else f"{r['quantity']} {r['unit']} (no price)"
                )
                lines.append(f"    - {r['item_name']}: {price_str}")
            lines.append(
                f"  IMPORTANT: When asked about total value for '{workspace_label}', "
                f"always report ${round(total_value, 2):.2f} — do NOT recompute."
            )

    return "\n".join(lines)


def _build_item_history_context(db: Session) -> str:
    """Most recent unit per item across ALL sessions — queries v_item_history view."""
    rows = db.execute(
        text("SELECT * FROM v_item_history LIMIT 80")
    ).mappings().all()

    if not rows:
        return ""
    lines = ["Known item unit history [source: v_item_history]:"]
    for r in rows:
        loc = f"{r['location_name']} › {r['storage_area']}" if r["location_name"] else r["storage_area"]
        price = f", unit_price=${r['unit_price']:.2f}" if r["unit_price"] else ""
        lines.append(
            f"  • {r['item_name']}: {r['unit']}{price} @ {loc} (last counted {r['count_date']})"
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
    logger.info(
        "USER INSIGHTS  worker=%s  emotion=%s  personality_note=%r  new_lexicons=%d",
        worker_id, user_emotion,
        (personality_note or "")[:80], len(new_lexicons),
    )

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
            logger.info("  LEXICON UPDATED  worker=%s  word=%r → %r  count=%d", worker_id, original, resolved, existing_lex.usage_count)
        else:
            db.add(UserLexicon(
                worker_id=worker_id,
                original_word=original,
                resolved_word=resolved or None,
                word_type=word_type,
            ))
            logger.info("  LEXICON ADDED  worker=%s  word=%r → %r  type=%s", worker_id, original, resolved, word_type)

    db.commit()
    logger.info("  USER PROFILE SAVED  worker=%s  emotion=%s", worker_id, user_emotion)


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
    # Use word-level matching — substring check causes false positives
    # e.g. "table" in "vegetables", "pen" in "open", "bag" in "cabbage"
    words = set(item_name.lower().split())
    return bool(words & NON_FOOD_KEYWORDS)


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
        logger.warning("DB WRITE SKIPPED  item_name=%r  quantity=%s  (missing data)", item_name, quantity)
        return False

    logger.info(
        "DB WRITE ──▶  item=%r  op=%s  qty=%s  unit=%r  area=%r  loc=%r  worker=%s  price=%s  expiry=%s",
        item_name, operation, quantity, unit, storage_area, location_name, worker_id, unit_price, expiry_date,
    )

    existing = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.item_name.ilike(f"%{item_name}%"),
            InventoryItem.storage_area == storage_area,
            InventoryItem.count_date == today,
        )
        .with_for_update()
        .first()
    )

    if existing:
        old_qty  = existing.quantity
        old_unit = existing.unit
        incoming_unit = normalize_unit(unit)
        stored_unit = normalize_unit(existing.unit)

        if incoming_unit != stored_unit and can_convert(stored_unit, incoming_unit):
            logger.info(
                "  DB UNIT CONVERSION  %s: %s → %s  [EDGE CASE]",
                item_name, stored_unit, incoming_unit,
            )
            existing_in_new = convert(existing.quantity, existing.unit, unit)
            if operation == "add":
                existing.quantity = round(existing_in_new + quantity, 4)
            elif operation == "subtract":
                existing.quantity = max(0, round(existing_in_new - quantity, 4))
            else:
                existing.quantity = quantity
            existing.unit = incoming_unit
        else:
            if stored_unit != incoming_unit:
                # Units are incompatible — cannot do math across unit systems.
                # Replace the old stock record with the new quantity in the new unit.
                logger.info(
                    "  DB UNIT SWITCH  %s: %s → %s  [INCOMPATIBLE UNIT CHANGE]  "
                    "replacing old qty=%s with new qty=%s (no cross-unit add)",
                    item_name, stored_unit, incoming_unit, existing.quantity, quantity,
                )
                existing.quantity = quantity
                existing.unit = normalize_unit(unit)
            elif operation == "add":
                existing.quantity = round(existing.quantity + quantity, 4)
                existing.unit = normalize_unit(unit)
            elif operation == "subtract":
                existing.quantity = max(0, round(existing.quantity - quantity, 4))
                existing.unit = normalize_unit(unit)
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

        logger.info(
            "  DB UPDATE  item=%r  BEFORE: %s %s  AFTER: %s %s  op=%s  area=%r  worker=%s",
            item_name, old_qty, old_unit, existing.quantity, existing.unit,
            operation, storage_area, worker_id,
        )
    else:
        new_item = InventoryItem(
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
        )
        db.add(new_item)
        logger.info(
            "  DB INSERT  item=%r  qty=%s  unit=%r  area=%r  cat=%r  price=%s  worker=%s  [NEW ITEM]",
            item_name, quantity, normalize_unit(unit), storage_area, category, unit_price, worker_id,
        )

    op_label = {"add": "Added", "subtract": "Removed", "set": "Set"}.get(operation, "Updated")
    log_details = f"{op_label} {quantity} {unit} of {item_name} at {storage_area} [{today}]"
    db.add(ActivityLog(
        action=op_label,
        item_name=item_name,
        details=log_details,
        worker=worker_id,
    ))
    logger.info(
        "DB WRITE ◀──  item=%r  op=%s  new_qty=%s  unit=%r  area=%r  worker=%s  [ActivityLog: %r]",
        item_name, op_label,
        existing.quantity if existing else quantity,
        unit, storage_area, worker_id, log_details,
    )
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
# Streaming helpers — per-node status events for the SSE /stream endpoint
# ──────────────────────────────────────────────

_NODE_LABELS: dict[str, tuple[str, str]] = {
    "load_context":   ("📦", "Context"),
    "load_memory":    ("🧬", "Memory"),
    "preprocess":     ("⚙️", "Preprocess"),
    "intent":         ("🧠", "Intent"),
    "clarify":        ("❓", "Clarify"),
    "guard":          ("🛡️", "Guard"),
    "rejected":       ("🚫", "Guard"),
    "extraction":     ("🔍", "Extract"),
    "priority":       ("📌", "Priority"),
    "aria":           ("✨", "ARIA"),
    "validate":       ("✔️", "Validate"),
    "execute":        ("💾", "Execute"),
    "persist_memory": ("💡", "Learn"),
}


def _trunc(s: str, n: int = 100) -> str:
    return (s[:n] + "…") if s and len(s) > n else (s or "")


def _node_status(node_name: str, output: dict, state: Optional[dict] = None) -> Optional[dict]:
    """Build a rich status event from a node's output + accumulated upstream state.

    Returns a dict with icon/label/detail/reasoning/input/output so the
    frontend can drill into what each agent received, thought, and decided.
    """
    label_info = _NODE_LABELS.get(node_name)
    if not label_info:
        return None
    icon, label = label_info
    s = state or {}

    if node_name == "load_context":
        inv = output.get("inventory_context") or ""
        hist = output.get("conversation_history") or []
        pending = output.get("pending_action")
        n_items = inv.count("•")
        parts = [f"{n_items} items in stock"]
        if hist:
            parts.append(f"{len(hist)} prior turns")
        if pending:
            parts.append("pending action")
        inv_lines = [l.strip() for l in inv.split("\n") if "•" in l][:10]
        recent_turns = [
            {"role": t.get("role", "?"), "said": _trunc(t.get("content", ""), 100)}
            for t in (hist[-4:] if hist else [])
        ]
        return {
            "icon": icon, "label": label, "detail": " · ".join(parts),
            "input": {
                "worker": s.get("worker_id", "?"),
                "storage_area": s.get("storage_area") or "none",
                "location": s.get("location_name") or "none",
            },
            "output": {
                "inventory_items": n_items,
                "inventory_data": inv_lines or None,
                "conversation_turns": len(hist),
                "recent_turns": recent_turns or None,
                "pending_action": bool(pending),
                "has_rejection_log": bool(output.get("rejection_context")),
                "has_session_digest": bool(output.get("session_digest")),
            },
        }

    if node_name == "load_memory":
        mems = output.get("user_memories") or []
        detail = f"{len(mems)} worker memories" if mems else "no memories yet"
        memory_texts = [
            _trunc((m.get("memory") or m.get("text") or "").strip(), 120)
            for m in mems[:5]
            if (m.get("memory") or m.get("text") or "").strip()
        ]
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {"worker": s.get("worker_id", "?"), "source": "Mem0"},
            "output": {
                "memories_loaded": len(mems),
                "profile_source": "Mem0" if mems else "DB (fallback)",
                "memories": memory_texts or None,
            },
        }

    if node_name == "preprocess":
        is_aff = output.get("is_affirmation", False)
        fuzzy = output.get("fuzzy_units_hint") or ""
        fragment = output.get("fragment_hint") or ""
        text = s.get("text", "")
        detail = ("affirmation → fast path" if is_aff
                  else "fuzzy unit detected" if fuzzy
                  else "clean")
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {
                "message": _trunc(text, 80),
                "pending_action_exists": bool(s.get("pending_action")),
            },
            "output": {
                "is_affirmation": is_aff,
                "fuzzy_units": _trunc(fuzzy, 80) if fuzzy else None,
                "fragment_hint": _trunc(fragment, 80) if fragment else None,
            },
        }

    if node_name == "intent":
        ir = output.get("intent_result") or {}
        intent = output.get("pre_classified_intent") or ir.get("intent", "?")
        conf = output.get("intent_confidence") or ir.get("confidence", 0.0)
        reasoning = ir.get("reasoning") or ""
        slot_item = ir.get("slot_item") or ""
        slot_qty = ir.get("slot_quantity")
        slot_unit = ir.get("slot_unit") or ""
        text = s.get("text", "")
        conv = s.get("conversation_history") or []
        pending = s.get("pending_action")
        detail = f"{intent} · {conf:.0%}"
        if slot_item:
            qty_str = f"{slot_qty} {slot_unit}".strip() if slot_qty is not None else ""
            detail += f" — {slot_item}" + (f" · {qty_str}" if qty_str else "")
        pending_summary = None
        if pending:
            p_items = pending.get("items") or []
            pending_summary = ", ".join(
                f"{i.get('quantity')} {i.get('unit','').strip()} {i.get('item_name','')}".strip()
                for i in p_items[:2] if i.get("item_name")
            ) or "yes"
        return {
            "icon": icon, "label": label, "detail": detail,
            "reasoning": _trunc(reasoning, 300) if reasoning else None,
            "input": {
                "message": _trunc(text, 100),
                "model": "gpt-4o-mini (temp=0)",
                "history_turns_shown": min(len(conv), 6),
                "pending_action": pending_summary or "none",
            },
            "output": {
                "intent": intent,
                "confidence": f"{conf:.0%}",
                "slot_item": slot_item or None,
                "slot_quantity": slot_qty,
                "slot_unit": slot_unit or None,
                "needs_clarification": ir.get("needs_clarification", False),
            },
        }

    if node_name == "guard":
        gr = output.get("guard_result") or {}
        rejected = output.get("guard_rejected", False)
        text = s.get("text", "")
        if not gr:
            detail = "skipped"
            items_out: list = []
        elif rejected:
            flagged = [i.get("name", "?") for i in gr.get("items", []) if not i.get("is_valid")]
            detail = "rejected — " + (", ".join(flagged[:3]) or "non-food item")
            items_out = [
                {"name": i.get("name"), "valid": i.get("is_valid"),
                 "ambiguous": i.get("is_ambiguous"), "concern": i.get("concern", "")}
                for i in gr.get("items", [])
            ]
        else:
            detail = "passed"
            items_out = [{"name": i.get("name"), "valid": True} for i in gr.get("items", [])]
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {
                "message": _trunc(text, 80),
                "model": "gpt-4o-mini",
                "word_count": len(text.split()),
            },
            "output": {
                "result": "rejected" if rejected else ("skipped" if not gr else "passed"),
                "items": items_out or None,
                "guard_message": gr.get("guard_message") or None,
            },
        }

    if node_name == "extraction":
        er = output.get("extraction_result") or {}
        text = s.get("text", "")
        if not er:
            return {
                "icon": icon, "label": label, "detail": "skipped",
                "input": {"reason": "affirmation / query / short message"},
                "output": {"items": None},
            }
        items = er.get("items") or []
        conf = (er.get("inventory_session") or {}).get("overall_confidence", "")
        if items:
            names = ", ".join(
                f"{i.get('canonical_name') or i.get('raw_text','?')} "
                f"{i.get('quantity','') or ''} {i.get('unit','') or ''}".strip()
                for i in items[:2]
            )
            detail = f"{len(items)} item{'s' if len(items)!=1 else ''} · {names}"
            if conf:
                detail += f" · {conf}"
        else:
            detail = "no items parsed"
        items_out = [
            {
                "raw": i.get("raw_text", "?"),
                "canonical": i.get("canonical_name", "?"),
                "category": i.get("category", "UNKNOWN"),
                "quantity": i.get("quantity"),
                "unit": i.get("unit", ""),
                "confidence": i.get("confidence", "?"),
                "catalog_match": i.get("matched_catalog_item") or "UNKNOWN",
                "errors": ", ".join(i.get("validation_errors") or []) or None,
            }
            for i in items
        ]
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {"message": _trunc(text, 80), "model": "gpt-4o"},
            "output": {
                "overall_confidence": conf or "?",
                "items": items_out or None,
            },
        }

    if node_name == "priority":
        focus = output.get("priority_focus") or ""
        ph = output.get("priority_conversation_history") or []
        filtered_inv = output.get("priority_inventory_context") or ""
        orig_inv = s.get("inventory_context") or ""
        orig_hist = len(s.get("conversation_history") or [])
        detail = f"focus: {focus}" if focus else f"{len(ph)} turns kept"
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {
                "inventory_items": orig_inv.count("•"),
                "history_turns": orig_hist,
                "intent": s.get("pre_classified_intent", "?"),
            },
            "output": {
                "focus": focus or "all items",
                "inventory_filtered_to": filtered_inv.count("•"),
                "history_kept": len(ph),
            },
        }

    if node_name == "aria":
        ar = output.get("aria_result") or {}
        action = ar.get("action", "?")
        items = (ar.get("data") or {}).get("items") or []
        emotion = ar.get("user_emotion") or ""
        message = ar.get("message") or ""
        flags = (ar.get("data") or {}).get("flags") or []
        new_lexicons = ar.get("new_lexicons") or []
        personality = ar.get("personality_note") or ""
        parts = [f"action={action}"]
        if items:
            parts.append(", ".join(
                f"{i.get('item_name','?')}"
                + (f" {i.get('quantity')} {i.get('unit','').strip()}".rstrip()
                   if i.get("quantity") is not None else "")
                for i in items[:2]
            ))
        if emotion and emotion not in ("neutral", ""):
            parts.append(emotion)
        # Build what ARIA saw from upstream context
        pri_inv = s.get("priority_inventory_context") or s.get("inventory_context") or ""
        pri_hist = s.get("priority_conversation_history") or s.get("conversation_history") or []
        pri_focus = s.get("priority_focus") or "all"
        ext_res = s.get("extraction_result") or {}
        ext_items = ext_res.get("items") or []
        ir = s.get("intent_result") or {}
        slot_parts = []
        if ir.get("slot_item"):
            slot_parts.append(f"item='{ir['slot_item']}'")
        if ir.get("slot_quantity") is not None:
            slot_parts.append(f"qty={ir['slot_quantity']}")
        if ir.get("slot_unit"):
            slot_parts.append(f"unit='{ir['slot_unit']}'")
        pending = s.get("pending_action")
        pending_summary = None
        if pending:
            p_items = pending.get("items") or []
            pending_summary = ", ".join(
                f"{i.get('quantity')} {i.get('unit','').strip()} {i.get('item_name','')}".strip()
                for i in p_items[:2] if i.get("item_name")
            ) or "yes"
        pri_inv_lines = [l.strip() for l in pri_inv.split("\n") if "•" in l][:8]
        ext_items_out = [
            {"raw": i.get("raw_text","?"), "canonical": i.get("canonical_name"),
             "qty": i.get("quantity"), "unit": i.get("unit",""), "confidence": i.get("confidence","?")}
            for i in ext_items[:4]
        ]
        items_out = [
            {"name": i.get("item_name"), "quantity": i.get("quantity"),
             "unit": i.get("unit",""), "operation": i.get("operation",""),
             "storage_area": i.get("storage_area",""), "category": i.get("category","")}
            for i in items
        ]
        return {
            "icon": icon, "label": label, "detail": " · ".join(parts),
            "input": {
                "model": "gpt-4o",
                "inventory_context_shown": pri_inv_lines or None,
                "history_turns_shown": len(pri_hist),
                "context_focus": pri_focus,
                "intent": s.get("pre_classified_intent", "?"),
                "slots": ", ".join(slot_parts) if slot_parts else "none",
                "extracted_items": ext_items_out or None,
                "pending_action": pending_summary or "none",
                "is_affirmation": s.get("is_affirmation", False),
            },
            "output": {
                "action": action,
                "message": message,
                "items": items_out or None,
                "flags": flags or None,
                "emotion": emotion or "neutral",
                "new_lexicons": [lx.get("original_word","") for lx in new_lexicons if lx.get("original_word")] or None,
                "personality_note": _trunc(personality, 150) if personality else None,
            },
        }

    if node_name == "validate":
        flags = output.get("extra_flags") or []
        ar = output.get("aria_result") or {}
        action = ar.get("action", "?")
        items = (ar.get("data") or {}).get("items") or []
        detail = f"flags: {', '.join(flags)}" if flags else f"clean · action={action}"
        _flag_info = {
            "unit_mismatch": "unit differs from stored history",
            "suspicious_quantity": "qty > 10× previous count",
            "storage_warning": "item unusual for this storage area",
            "conflict": "same-day write conflict",
            "not_relevant": "non-food item",
        }
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {
                "action": action,
                "items": [{"name": i.get("item_name"), "qty": i.get("quantity"),
                           "unit": i.get("unit",""), "category": i.get("category","")}
                          for i in items[:4]] or None,
            },
            "output": {
                "action_after": action,
                "flags": [{"flag": f, "meaning": _flag_info.get(f, f)} for f in flags] or None,
                "clean": not bool(flags),
            },
        }

    if node_name == "execute":
        updated = output.get("inventory_updated", False)
        action = output.get("action", "?")
        items = (output.get("data") or {}).get("items") or []
        flags = (output.get("data") or {}).get("flags") or []
        if updated and items:
            names = ", ".join(
                f"{i.get('item_name','?')} {i.get('quantity','')} {i.get('unit','')}".strip()
                for i in items[:2] if i.get("item_name")
            )
            detail = f"saved · {names}" if names else "inventory updated"
        elif action == "confirm":
            detail = "stored · awaiting confirmation"
        else:
            detail = "no DB write"
        items_written = [
            {"name": i.get("item_name"), "qty": i.get("quantity"),
             "unit": i.get("unit",""), "op": i.get("operation",""), "area": i.get("storage_area","")}
            for i in items if i.get("item_name")
        ] if updated else None
        return {
            "icon": icon, "label": label, "detail": detail,
            "input": {"action": action, "items_to_process": len(items)},
            "output": {
                "inventory_updated": updated,
                "pending_stored": action == "confirm" and not updated,
                "items_written": items_written,
                "flags": flags or None,
            },
        }

    if node_name in ("clarify", "rejected"):
        msg = output.get("message") or ""
        ir = s.get("intent_result") or {}
        return {
            "icon": icon, "label": label, "detail": _trunc(msg, 70),
            "input": {
                "intent": ir.get("intent", "?"),
                "confidence": f"{ir.get('confidence',0):.0%}",
                "reason": node_name,
            },
            "output": {"message_to_worker": msg},
        }

    if node_name == "persist_memory":
        return {"icon": icon, "label": label, "detail": "worker insights saved"}

    return {"icon": icon, "label": label, "detail": ""}


# ──────────────────────────────────────────────
# Guard crew wrapper (used by guard_node in workflow/nodes.py)
# ──────────────────────────────────────────────

async def _guard_validate_items(text: str) -> dict:
    from agents.guard_agent import guard_validate
    return await guard_validate(text)


# ──────────────────────────────────────────────
# Main processing function — LangGraph entry point
# ──────────────────────────────────────────────

async def process_message(
    text: str,
    session_id: str,
    worker_id: str,
    db: Session,
    storage_area: Optional[str] = None,
    location_name: Optional[str] = None,
    messages: Optional[list] = None,
) -> dict:
    """
    Route a worker's voice message through the LangGraph workflow:

        START
          ↓ (parallel)
        load_context_node + load_memory_node   ← DB context + Mem0 worker memories
          ↓ (fan-in)
        preprocess_node                        ← fuzzy units, affirmation, fragment hints
          ↓
        guard_node                             ← validate items via CrewAI Guard
          ↓ (conditional)
        ├── rejected_node → END
        └── aria_node                          ← main ARIA CrewAI agent
              ↓
          validate_node                        ← Python-level safety checks
              ↓
          execute_node                         ← DB writes + conversation save
              ↓
          persist_memory_node                  ← Mem0 writeback
              ↓
             END
    """
    from workflow.graph import get_graph

    graph = get_graph()

    initial_state = {
        "text": text,
        "session_id": session_id,
        "worker_id": worker_id,
        "storage_area": storage_area or "",
        "location_name": location_name or "",
        "db": db,
        "extra_flags": [],
        # If the client sent conversation history, pre-seed it so load_context_node
        # skips the DB query and uses the client's version instead.
        **({"conversation_history": messages} if messages else {}),
    }

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("GRAPH FAILED  error_type=%s  error=%s", type(exc).__name__, exc, exc_info=True)
        return {
            "message": "The AI service failed — please try again in a moment.",
            "action": "none",
            "data": {"items": [], "confirmed": False, "flags": []},
            "inventory_updated": False,
            "session_id": session_id,
        }

    return {
        "message": final_state.get("message", ""),
        "action": final_state.get("action", "none"),
        "data": final_state.get("data", {"items": [], "confirmed": False, "flags": []}),
        "inventory_updated": final_state.get("inventory_updated", False),
        "session_id": session_id,
    }


async def process_message_stream(
    text: str,
    session_id: str,
    worker_id: str,
    db: Session,
    storage_area: Optional[str] = None,
    location_name: Optional[str] = None,
    messages: Optional[list] = None,
) -> AsyncGenerator[dict, None]:
    """
    Async generator for the SSE /stream endpoint.

    Yields dicts with a "type" key:
      {"type": "status",  "step": node_name, "phase": "start"|"done",
       "icon": "📦", "label": "Context", "detail": "..."}
      {"type": "chunk",   "text": "..."}          ← response text chunks
      {"type": "done",    "message": "...", ...}   ← full result payload
      {"type": "error",   "message": "..."}        ← on failure
    """
    from workflow.graph import get_graph

    graph = get_graph()
    initial_state = {
        "text": text,
        "session_id": session_id,
        "worker_id": worker_id,
        "storage_area": storage_area or "",
        "location_name": location_name or "",
        "db": db,
        "extra_flags": [],
        **({"conversation_history": messages} if messages else {}),
    }

    _TERMINAL_NODES = {"execute", "clarify", "rejected"}
    final_result: dict = {}
    accumulated_state: dict = {
        "text": text,
        "worker_id": worker_id,
        "session_id": session_id,
        "storage_area": storage_area or "",
        "location_name": location_name or "",
    }

    try:
        async for chunk in graph.astream(initial_state):
            for node_name, output in chunk.items():
                if node_name not in _NODE_LABELS:
                    continue
                # Accumulate state so downstream status events can see upstream context
                if isinstance(output, dict):
                    accumulated_state.update(output)
                status = _node_status(node_name, output if isinstance(output, dict) else {}, accumulated_state)
                if status:
                    yield {"type": "status", "step": node_name, "phase": "done", **status}
                if node_name in _TERMINAL_NODES:
                    final_result = output

    except Exception as exc:
        logger.error("STREAM GRAPH FAILED  error_type=%s  error=%s", type(exc).__name__, exc, exc_info=True)
        yield {"type": "error", "message": "The AI service failed — please try again in a moment."}
        return

    if not final_result:
        yield {"type": "error", "message": "No response received from the AI."}
        return

    # Stream message text chunk by chunk (simulate typing)
    message = final_result.get("message", "")
    for i in range(0, len(message), 4):
        yield {"type": "chunk", "text": message[i:i + 4]}
        await asyncio.sleep(0.012)

    yield {
        "type": "done",
        "message": message,
        "action": final_result.get("action", "none"),
        "data": final_result.get("data", {"items": [], "confirmed": False, "flags": []}),
        "inventory_updated": final_result.get("inventory_updated", False),
        "session_id": session_id,
    }
