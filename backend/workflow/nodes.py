"""
LangGraph nodes for the ARIA voice-inventory workflow.

Graph topology (built in graph.py):

    START
      ↓ (parallel fan-out — disjoint state keys, no conflict)
    load_context_node        load_memory_node
      ↓                         ↓
      └────────────┬────────────┘
                   ↓  (fan-in)
             preprocess_node
                   ↓
              guard_node
                   ↓  (conditional)
          ┌────────┴────────┐
       "rejected"         "aria"
          ↓                  ↓
    rejected_node        aria_node
          ↓                  ↓
         END          validate_node
                           ↓
                     execute_node
                           ↓
                  persist_memory_node
                           ↓
                          END
"""

import asyncio
import json
import logging
import time
from datetime import date as date_type

from workflow.state import WorkflowState
from clients.mem0_client import (
    get_user_memories,
    add_user_memory,
    build_user_profile_from_memories,
)
from logging_config import J

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# load_context_node  (parallel branch 1 — DB context)
# ─────────────────────────────────────────────────────────────────────────────

async def load_context_node(state: WorkflowState) -> dict:
    """
    Load all DB-derived context for this turn:
      - today's inventory state
      - item unit history
      - session conversation history
      - in-flight pending action (if any)
    Runs in parallel with load_memory_node; writes only disjoint state keys.
    """
    from services.ai_service import (
        _build_inventory_context,
        _build_item_history_context,
        _get_conversation_history,
        _get_or_create_user_profile,
        _pending_actions,
    )

    db = state["db"]
    session_id = state["session_id"]
    worker_id = state["worker_id"]
    storage_area = state.get("storage_area") or ""
    location_name = state.get("location_name") or ""
    t0 = time.perf_counter()

    logger.info(
        "NODE ──▶ load_context  worker=%s  session=%s  area=%r  location=%r",
        worker_id, session_id, storage_area, location_name,
    )

    # Eagerly create user profile row if this is a first-time worker
    try:
        _get_or_create_user_profile(worker_id, db)
    except Exception as exc:
        logger.warning("load_context  user profile init failed: %s", exc)

    inventory_context = _build_inventory_context(
        db, storage_area=storage_area, location_name=location_name
    )
    item_history_context = _build_item_history_context(db)
    conversation_history = _get_conversation_history(session_id, db, limit=30)
    pending_action = _pending_actions.get(session_id)

    # Count inventory lines for the log
    inv_item_count = inventory_context.count("•")
    hist_item_count = item_history_context.count("•")

    logger.info(
        "NODE ◀── load_context  inventory_items=%d  history_items=%d  "
        "conv_turns=%d  pending_action=%s  elapsed=%.0fms",
        inv_item_count, hist_item_count,
        len(conversation_history), bool(pending_action),
        (time.perf_counter() - t0) * 1000,
    )
    if pending_action:
        logger.info("  PENDING ACTION  %s", J(pending_action))

    return {
        "inventory_context": inventory_context,
        "item_history_context": item_history_context,
        "conversation_history": conversation_history,
        "pending_action": pending_action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# load_memory_node  (parallel branch 2 — Mem0)
# ─────────────────────────────────────────────────────────────────────────────

async def load_memory_node(state: WorkflowState) -> dict:
    """
    Load worker memories from Mem0 and format them for ARIA's prompt.

    Falls back to the DB-based user profile when Mem0 returns nothing
    (cold start or API key not configured).
    Runs in parallel with load_context_node; writes only disjoint state keys.
    """
    from services.ai_service import _build_user_profile_context

    worker_id = state["worker_id"]
    db = state["db"]
    t0 = time.perf_counter()

    logger.info("NODE ──▶ load_memory  worker=%s", worker_id)

    user_memories = await get_user_memories(worker_id)
    mem0_profile = build_user_profile_from_memories(user_memories)

    source = "mem0" if mem0_profile else "db"
    user_profile_context = mem0_profile or _build_user_profile_context(worker_id, db)

    if not mem0_profile and user_memories:
        logger.warning("load_memory  [FALLBACK]  mem0 returned %d records but profile empty → using DB profile", len(user_memories))
    elif not mem0_profile:
        logger.info("load_memory  mem0 empty (cold start or disabled) → [FALLBACK] using DB profile")

    logger.info(
        "NODE ◀── load_memory  worker=%s  mem0_memories=%d  profile_source=%s  elapsed=%.0fms",
        worker_id, len(user_memories), source, (time.perf_counter() - t0) * 1000,
    )
    for m in user_memories[:5]:
        mem_text = (m.get("memory") or m.get("text") or "")[:100]
        logger.debug("  MEM0 RECORD  id=%s  text=%r", m.get("id", "?"), mem_text)

    return {
        "user_memories": user_memories,
        "user_profile_context": user_profile_context,
    }


# ─────────────────────────────────────────────────────────────────────────────
# preprocess_node  (runs after both parallel branches converge)
# ─────────────────────────────────────────────────────────────────────────────

def preprocess_node(state: WorkflowState) -> dict:
    """
    Lightweight preprocessing — no I/O:
      - detect fuzzy unit typos ("lbs" → "lb")
      - detect fragment completion patterns across turns
      - detect affirmations ("yes", "yep", "ok") against a pending action
    """
    from services.ai_service import _is_affirmation, _build_completion_hint
    from utils.unit_converter import extract_fuzzy_units

    text = state["text"]
    conversation_history = state.get("conversation_history") or []
    pending_action = state.get("pending_action")

    logger.info("NODE ──▶ preprocess  text=%r  history_turns=%d", text[:120], len(conversation_history))

    # Fuzzy unit detection
    fuzzy_hits = extract_fuzzy_units(text)
    if fuzzy_hits:
        pairs = ", ".join(f"'{o}' → '{c}'" for o, c in fuzzy_hits)
        fuzzy_units_hint = (
            f"Fuzzy unit typos detected in this message: {pairs}. "
            f"Suggest the correction to the worker (action='clarify') and use the corrected unit."
        )
        logger.info("  FUZZY UNITS DETECTED  %s", pairs)
    else:
        fuzzy_units_hint = ""

    fragment_hint = _build_completion_hint(text, conversation_history)
    is_affirmation = _is_affirmation(text) and pending_action is not None

    if fragment_hint:
        logger.info("  FRAGMENT HINT DETECTED  [EDGE CASE] %s", fragment_hint[:120])
    if is_affirmation:
        pending_items = (pending_action or {}).get("items", [])
        logger.info(
            "  AFFIRMATION DETECTED  [EDGE CASE] text=%r  pending_items=%d  "
            "→ will short-circuit ARIA and execute directly",
            text[:60], len(pending_items),
        )

    logger.info(
        "NODE ◀── preprocess  fuzzy=%s  fragment=%s  is_affirmation=%s",
        bool(fuzzy_hits), bool(fragment_hint), is_affirmation,
    )

    return {
        "fuzzy_units_hint": fuzzy_units_hint,
        "fragment_hint": fragment_hint,
        "is_affirmation": is_affirmation,
    }


# ─────────────────────────────────────────────────────────────────────────────
# intent_node  — dedicated intent classifier (runs before guard)
# ─────────────────────────────────────────────────────────────────────────────

async def intent_node(state: WorkflowState) -> dict:
    """
    Classify the worker's intent with a focused gpt-4o-mini call (temp=0).

    Running a separate cheap classifier before ARIA ensures the intent label
    is a primary output, not a side-effect.  The result is injected into
    ARIA's user message as a hard constraint so ARIA cannot reclassify and
    produce a different answer.

    confidence < 0.65 → sets needs_clarification=True → graph routes to
    clarify_node and short-circuits before guard or ARIA.
    """
    from agents.intent_agent import classify_intent

    text = state["text"]
    conversation_history = state.get("conversation_history") or []
    pending_action = state.get("pending_action")

    logger.info("NODE ──▶ intent  text=%r", text[:120])

    intent_result = await classify_intent(
        text=text,
        conversation_history=conversation_history,
        pending_action=pending_action,
    )

    needs_clarification = bool(
        intent_result.get("needs_clarification")
        or (
            intent_result.get("confidence", 1.0) < 0.65
            and intent_result.get("intent") == "unknown"
        )
    )

    logger.info(
        "NODE ◀── intent  intent=%s  confidence=%.2f  needs_clarify=%s",
        intent_result.get("intent"), intent_result.get("confidence", 0),
        needs_clarification,
    )

    return {
        "intent_result":         intent_result,
        "pre_classified_intent": intent_result.get("intent", "unknown"),
        "intent_confidence":     intent_result.get("confidence", 0.0),
        "needs_clarification":   needs_clarification,
    }


def route_after_intent(state: WorkflowState) -> str:
    intent_result = state.get("intent_result") or {}
    intent = intent_result.get("intent", "?")
    conf = intent_result.get("confidence", 0.0)
    needs = state.get("needs_clarification", False)
    if needs:
        reason = f"confidence={conf:.2f} < 0.65" if conf < 0.65 else "needs_clarification=True from classifier"
        logger.info("ROUTE after_intent → clarify  WHY: %s  intent=%s  [EDGE CASE]", reason, intent)
        return "clarify"
    logger.info("ROUTE after_intent → guard  WHY: intent=%s  confidence=%.2f  (sufficient)", intent, conf)
    return "guard"


# ─────────────────────────────────────────────────────────────────────────────
# clarify_node  — low-confidence intent short-circuit
# ─────────────────────────────────────────────────────────────────────────────

_SMALL_TALK_TRIGGERS = {
    "wow", "great", "nice", "cool", "awesome", "amazing", "fantastic",
    "good", "excellent", "perfect", "wonderful", "brilliant", "superb",
    "thanks", "thank", "cheers", "appreciate", "hey", "hi", "hello",
    "morning", "afternoon", "evening", "howdy", "yo", "sup",
    "haha", "lol", "hehe", "ok", "okay", "alright", "sure", "yep",
    "find", "found", "interesting", "noted", "got", "gotcha",
}

_INVENTORY_SIGNALS = {
    "add", "remove", "set", "check", "how", "much", "stock", "kg",
    "liters", "pieces", "packets", "boxes", "bottles", "total", "value",
    "expiry", "expire", "when", "does",
}


def _is_small_talk(text: str) -> bool:
    """True when the message has no inventory signals and reads as casual chat."""
    words = set(text.lower().split())
    has_inventory = bool(words & _INVENTORY_SIGNALS)
    all_social = words.issubset(_SMALL_TALK_TRIGGERS | {"a", "an", "the", "and", "or", "is", "it"})
    return not has_inventory and (all_social or len(words) <= 4 and bool(words & _SMALL_TALK_TRIGGERS))


def clarify_node(state: WorkflowState) -> dict:
    """
    Short-circuit when intent classifier cannot classify with confidence ≥ 0.65.
    Saves the exchange to DB and returns a domain-specific clarification prompt.
    """
    from models import ConversationMessage

    intent_result = state.get("intent_result") or {}
    text = state["text"]
    session_id = state["session_id"]
    db = state["db"]

    if _is_small_talk(text):
        question = "Happy to help! What would you like to add, remove, or check today?"
    else:
        question = (
            intent_result.get("clarification_question")
            or (
                "I'm not quite sure what you'd like to do. "
                "Try: 'add [qty] [unit] [item]', 'remove [qty] [unit] [item]', "
                "or 'how much [item] do we have?'"
            )
        )

    logger.info(
        "NODE ──▶ clarify  session=%s  text=%r  confidence=%.2f",
        session_id, text[:80], intent_result.get("confidence", 0),
    )

    db.add(ConversationMessage(session_id=session_id, role="user", content=text))
    db.add(ConversationMessage(
        session_id=session_id, role="assistant",
        content=question, action_taken="clarify",
    ))
    db.commit()

    logger.info("  DB SAVED  session=%s  user=%r  assistant=%r  action=clarify", session_id, text[:60], question[:80])
    logger.info("NODE ◀── clarify  question=%r  [short-circuit → END]", question[:120])

    return {
        "message":           question,
        "action":            "clarify",
        "intent":            "unknown",
        "data":              {"items": [], "confirmed": False, "flags": ["low_confidence_intent"]},
        "inventory_updated": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# guard_node  — item validity check
# ─────────────────────────────────────────────────────────────────────────────

async def guard_node(state: WorkflowState) -> dict:
    """
    Run the Guard CrewAI agent to verify items are legitimate restaurant inventory.

    Skipped for very short messages and when the last assistant turn was
    a clarification request — those are almost always confirmations or
    fragment completions rather than new item introductions.
    """
    from agents.guard_agent import guard_validate

    text = state["text"]
    conversation_history = state.get("conversation_history") or []

    word_count = len(text.split())
    last_was_clarify = (
        conversation_history
        and conversation_history[-1].get("role") == "assistant"
        and any(
            kw in conversation_history[-1].get("content", "").lower()
            for kw in (
                "did you mean", "could you clarify", "please confirm",
                "please specify", "unrecognized", "referring to",
                "what item", "which item", "tell me what", "item to add",
                "quantity", "how much", "how many", "what unit", "can you tell me",
            )
        )
    )
    skip_guard = word_count <= 3 or last_was_clarify

    logger.info(
        "NODE ──▶ guard  text=%r  word_count=%d  skip=%s  reason=%s",
        text[:80], word_count, skip_guard,
        "short_message" if word_count <= 3 else ("last_was_clarify" if last_was_clarify else "none"),
    )

    if skip_guard:
        logger.info("NODE ◀── guard  SKIPPED  guard_rejected=False")
        return {"guard_result": {}, "guard_rejected": False}

    guard_result = await guard_validate(text)
    guard_rejected = bool(
        guard_result.get("has_items") and not guard_result.get("all_valid")
    )
    logger.info(
        "NODE ◀── guard  has_items=%s  all_valid=%s  guard_rejected=%s",
        guard_result.get("has_items"), guard_result.get("all_valid"), guard_rejected,
    )
    return {"guard_result": guard_result, "guard_rejected": guard_rejected}


# ─────────────────────────────────────────────────────────────────────────────
# Route after guard
# ─────────────────────────────────────────────────────────────────────────────

def route_after_guard(state: WorkflowState) -> str:
    rejected = state.get("guard_rejected", False)
    guard_result = state.get("guard_result") or {}
    if rejected:
        flagged = [i.get("name") for i in guard_result.get("items", []) if not i.get("is_valid") or i.get("is_ambiguous")]
        logger.warning("ROUTE after_guard → rejected  WHY: invalid/ambiguous items=%s  [EDGE CASE]", flagged)
        return "rejected"
    skipped = not guard_result
    reason = "guard was skipped (short msg or last-turn clarify)" if skipped else "all items valid"
    logger.info("ROUTE after_guard → extraction  WHY: %s", reason)
    return "extraction"


# ─────────────────────────────────────────────────────────────────────────────
# rejected_node  — guard-rejected path
# ─────────────────────────────────────────────────────────────────────────────

def rejected_node(state: WorkflowState) -> dict:
    """
    Guard rejected the message — save the clarification exchange to DB
    and return without touching inventory.
    """
    from models import ConversationMessage

    guard_result = state.get("guard_result") or {}
    text = state["text"]
    session_id = state["session_id"]
    db = state["db"]

    guard_message = (
        guard_result.get("guard_message")
        or "Could you clarify what you mean by that item?"
    )

    flagged_items = [i.get("name") for i in guard_result.get("items", []) if not i.get("is_valid") or i.get("is_ambiguous")]
    logger.info(
        "NODE ──▶ rejected  session=%s  text=%r  flagged_items=%s  guard_message=%r",
        session_id, text[:80], flagged_items, guard_message[:100],
    )

    db.add(ConversationMessage(session_id=session_id, role="user", content=text))
    db.add(ConversationMessage(
        session_id=session_id, role="assistant",
        content=guard_message, action_taken="clarify",
    ))
    db.commit()

    logger.info("  DB SAVED  session=%s  user+assistant  action=guard_reject  [→ END]", session_id)

    return {
        "message": guard_message,
        "action": "clarify",
        "intent": "clarify",
        "data": {
            "items": guard_result.get("items", []),
            "confirmed": False,
            "flags": ["incomplete"],
            "guard": True,
        },
        "inventory_updated": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# extraction_node  — strict inventory extraction (runs after guard, before aria)
# ─────────────────────────────────────────────────────────────────────────────

async def extraction_node(state: WorkflowState) -> dict:
    """
    Run the Hotel Inventory Extraction AI on the worker's message.

    Produces a strict structured extraction (ExtractionResult) before ARIA
    so ARIA receives pre-validated items rather than raw text.  This separates
    parsing accuracy from conversational response generation.

    Skipped for non-inventory intents (query, analytics, deny, confirm) and
    for very short messages, since those are confirmations or fragments —
    extraction would be meaningless.
    """
    from agents.extraction_agent import extract_inventory

    text = state["text"]
    pre_classified_intent = state.get("pre_classified_intent") or "unknown"
    is_affirmation = state.get("is_affirmation", False)

    skip_intents = {"query", "analytics", "deny", "confirm"}
    word_count = len(text.split())

    skip = is_affirmation or pre_classified_intent in skip_intents or word_count <= 2
    skip_reason = (
        "affirmation" if is_affirmation
        else f"intent={pre_classified_intent}" if pre_classified_intent in skip_intents
        else "short_message" if word_count <= 2
        else None
    )

    logger.info(
        "NODE ──▶ extraction  text=%r  intent=%s  word_count=%d  skip=%s  reason=%s",
        text[:80], pre_classified_intent, word_count, skip, skip_reason,
    )

    if skip:
        logger.info("NODE ◀── extraction  SKIPPED")
        return {"extraction_result": {}}

    try:
        extraction_result = await extract_inventory(text)
    except Exception as exc:
        logger.warning("extraction_node  [FALLBACK]  error=%s — skipping, ARIA sees raw text", exc)
        extraction_result = {}

    items = extraction_result.get("items", []) if extraction_result else []
    logger.info("NODE ◀── extraction  items=%d", len(items))

    return {"extraction_result": extraction_result}


# ─────────────────────────────────────────────────────────────────────────────
# aria_node  — main ARIA CrewAI agent
# ─────────────────────────────────────────────────────────────────────────────

async def aria_node(state: WorkflowState) -> dict:
    """
    Invoke the ARIA agent with full context built from earlier nodes.
    Memory-enriched user_profile_context (from load_memory_node) is injected
    so ARIA can adapt tone, recognise custom vocabulary, and recall patterns.
    """
    from agents.aria_agent import aria_process

    text = state["text"]
    worker_id = state["worker_id"]
    storage_area = state.get("storage_area") or ""
    location_name = state.get("location_name") or ""
    session_id = state["session_id"]

    inventory_context = state.get("inventory_context") or ""
    item_history_context = state.get("item_history_context") or ""
    conversation_history = state.get("conversation_history") or []
    user_profile_context = state.get("user_profile_context") or ""
    fuzzy_units_hint = state.get("fuzzy_units_hint") or ""
    fragment_hint = state.get("fragment_hint") or ""
    pending_action = state.get("pending_action")
    is_affirmation = state.get("is_affirmation", False)

    # Inject pre-classified intent and extracted slots as hard constraints
    pre_classified_intent = state.get("pre_classified_intent") or ""
    intent_result = state.get("intent_result") or {}
    slot_parts: list[str] = []
    if intent_result.get("slot_item"):
        slot_parts.append(f"item={intent_result['slot_item']!r}")
    if intent_result.get("slot_quantity") is not None:
        slot_parts.append(f"quantity={intent_result['slot_quantity']}")
    if intent_result.get("slot_unit"):
        slot_parts.append(f"unit={intent_result['slot_unit']!r}")
    intent_slots = ", ".join(slot_parts)

    # Build extraction context from extraction_node output — injected into ARIA prompt
    extraction_result = state.get("extraction_result") or {}
    extraction_items = extraction_result.get("items") or []
    if extraction_items:
        extraction_lines = ["## Pre-extracted Inventory Items (strict extraction — trust these over raw speech)"]
        for ei in extraction_items:
            cname = ei.get("canonical_name") or ei.get("raw_text") or "?"
            qty = ei.get("quantity")
            unit = ei.get("unit", "UNKNOWN")
            cat = ei.get("category", "UNKNOWN")
            conf = ei.get("confidence", "LOW")
            matched = ei.get("matched_catalog_item", "UNKNOWN")
            needs_confirm = ei.get("requires_confirmation", True)
            errs = ei.get("validation_errors") or []
            line = (
                f"  • {cname}: qty={qty}, unit={unit}, category={cat}, "
                f"catalog={matched}, confidence={conf}, confirm={needs_confirm}"
            )
            if errs:
                line += f", errors={errs}"
            extraction_lines.append(line)
        overall_conf = extraction_result.get("inventory_session", {}).get("overall_confidence", "LOW")
        extraction_lines.append(f"  Overall extraction confidence: {overall_conf}")
        extraction_context = "\n".join(extraction_lines)
    else:
        extraction_context = "No structured extraction available for this message."

    conversation_history_json = json.dumps(conversation_history, indent=2)

    workspace_storage = storage_area or "General Storage"
    if storage_area:
        workspace_context = (
            f"Location: {location_name or 'Unknown'}\n"
            f"Storage Area: {workspace_storage}\n"
            f"IMPORTANT: These values are FIXED by the UI. Always use '{workspace_storage}' "
            f"as storage_area in every item, regardless of what the worker says in speech."
        )
    else:
        workspace_context = (
            "No specific workspace set. Use 'General Storage' as default storage_area."
        )

    if is_affirmation:
        pending_action_context = (
            f"⚡ AFFIRMATION DETECTED — worker said '{text}' which means YES/CONFIRMED.\n"
            f"Execute this NOW: action='update', confirmed=true.\n"
            f"Pending:\n{json.dumps(pending_action, indent=2)}"
        )
    elif pending_action:
        pending_action_context = (
            f"Pending confirmation for this session:\n{json.dumps(pending_action, indent=2)}\n"
            "If the worker says yes/confirm/proceed/that's correct, "
            "execute this (action='update', confirmed=true)."
        )
    else:
        pending_action_context = "No pending action."

    if fragment_hint:
        pending_action_context = fragment_hint + "\n\n" + pending_action_context

    logger.info(
        "NODE ──▶ aria  worker=%s  session=%s  text=%r  intent=%s  affirmation=%s",
        worker_id, session_id, text[:80], pre_classified_intent, is_affirmation,
    )

    # Short-circuit: worker confirmed a pending action → skip LLM, execute directly.
    # This prevents ARIA from re-asking its clarification question (unit switch, storage
    # warning, etc.) when the worker already said yes to proceed.
    if is_affirmation and pending_action and pending_action.get("items"):
        confirmed_data = dict(pending_action)
        confirmed_data["confirmed"] = True
        logger.info(
            "NODE ◀── aria  SHORT-CIRCUIT (affirmation)  pending_items=%d  action=update",
            len(pending_action.get("items", [])),
        )
        return {
            "aria_result": {
                "message": "Got it, I'll go ahead!",
                "action": "update",
                "intent": "confirm",
                "data": confirmed_data,
                "user_emotion": "neutral",
                "new_lexicons": [],
                "personality_note": None,
            }
        }

    try:
        aria_result = await aria_process(
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
            pre_classified_intent=pre_classified_intent,
            intent_slots=intent_slots,
            extraction_context=extraction_context,
        )
    except Exception as exc:
        err_type = (
            "timed out"
            if "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
            else "failed"
        )
        logger.error("[aria_node] %s: %s", err_type, exc)
        aria_result = {
            "message": f"The AI service {err_type} — please try again in a moment.",
            "action": "none",
            "intent": "none",
            "data": {"items": [], "confirmed": False, "flags": []},
            "user_emotion": "neutral",
            "new_lexicons": [],
            "personality_note": "",
        }
        logger.error("NODE ◀── aria  ERROR  err_type=%s", err_type)

    else:
        logger.info(
            "NODE ◀── aria  action=%s  intent=%s  emotion=%s  items=%d  flags=%s",
            aria_result.get("action"), aria_result.get("intent"),
            aria_result.get("user_emotion"),
            len(aria_result.get("data", {}).get("items", [])),
            aria_result.get("data", {}).get("flags", []),
        )

    return {"aria_result": aria_result}


# ─────────────────────────────────────────────────────────────────────────────
# validate_node  — Python-level validation on top of ARIA's output
# ─────────────────────────────────────────────────────────────────────────────

def validate_node(state: WorkflowState) -> dict:
    """
    Apply deterministic Python-level validation that doesn't require LLM reasoning:
      - non-food item rejection
      - unit-conflict detection
      - suspicious quantity detection (10× previous count)
      - storage-type appropriateness (meat in freezer ✓, rice in freezer ?)
      - same-day conflict detection (another worker counted differently)

    Also locks the storage_area for pending confirm cards to the UI workspace value.
    """
    from services.ai_service import (
        _is_non_food_item,
        _is_generic_category,
        _generic_category_hint,
        _detect_unit_conflict,
        _detect_suspicious_quantity,
        _check_storage_appropriateness,
        _detect_same_day_conflict,
        _normalize_items,
        _pending_actions,
    )

    aria_result = dict(state.get("aria_result") or {})
    db = state["db"]
    storage_area = state.get("storage_area") or ""
    worker_id = state["worker_id"]
    session_id = state["session_id"]

    action = aria_result.get("action", "none")
    data = dict(aria_result.get("data") or {})
    data.setdefault("items", [])
    message = aria_result.get("message", "")
    workspace_storage = storage_area or "General Storage"
    items_list = _normalize_items(data)
    # Raw items include entries with null quantity — needed for generic/non-food checks
    raw_items_list = [i for i in data.get("items", []) if i.get("item_name")]
    extra_flags: list[str] = []

    logger.info(
        "NODE ──▶ validate  session=%s  action=%s  items=%d  workspace=%r",
        session_id, action, len(items_list), workspace_storage,
    )

    # Non-food rejection (check raw list so items with null qty are caught)
    non_food = [
        i["item_name"]
        for i in raw_items_list
        if _is_non_food_item(i.get("item_name", ""))
    ]
    if non_food:
        names = ", ".join(non_food)
        plural = "don't" if len(non_food) > 1 else "doesn't"
        action = "none"
        message = (
            f"I can only manage food, beverage, and kitchen supply inventory. "
            f"'{names}' {plural} appear to be food or kitchen items. "
            f"Please check if you meant something else."
        )
        extra_flags.append("not_relevant")
        logger.warning("validate  NON-FOOD REJECTED  items=%s", non_food)

    # Generic category rejection — check raw list so items with null qty are caught
    if not non_food:
        generic_items = [
            i["item_name"]
            for i in raw_items_list
            if _is_generic_category(i.get("item_name", ""))
        ]
        if generic_items:
            name = generic_items[0]
            hint = _generic_category_hint(name)
            action = "clarify"
            message = (
                f"'{name.capitalize()}' is a general category, not a specific inventory item. "
                f"Could you tell me exactly what you'd like to add? "
                f"For example: {hint}."
            )
            extra_flags.append("not_relevant")
            logger.warning("validate  GENERIC TERM REJECTED  items=%s", generic_items)

    # ── Collect ALL deterministic warnings first, then apply once ──────────────
    #
    # All four checks run regardless of whether ARIA already returned "clarify"
    # so that multiple issues (unit mismatch + storage mismatch) are always
    # surfaced together in a single message.
    warn_texts: list[str] = []

    # Detect phrases ARIA already embedded — suppress our duplicate for same topic
    _aria_has_unit_warn = any(
        phrase in message.lower()
        for phrase in ("switch to", "switch units", "would you like to switch",
                       "typically measured", "usually measured",
                       "measured by weight", "measured by volume")
    )
    _aria_has_storage_warn = any(
        phrase in message.lower()
        for phrase in ("typically stored", "usually stored", "not the fridge",
                       "not cold storage", "not dry storage", "not freezer",
                       "not bar", "not cellar")
    )

    # 1. Unit mismatch — incompatible unit vs. stored history
    if action not in ("update", "none"):
        for item in items_list:
            if item.get("item_name") and item.get("unit"):
                old_unit = _detect_unit_conflict(item["item_name"], item["unit"], db)
                if old_unit:
                    extra_flags.append("unit_mismatch")
                    logger.warning(
                        "validate  UNIT MISMATCH  item=%r  stored=%r  incoming=%r",
                        item["item_name"], old_unit, item["unit"],
                    )
                    if not _aria_has_unit_warn:
                        warn_texts.append(
                            f"⚠ Unit switch: {item['item_name']} is currently stored in "
                            f"{old_unit} but you said {item['unit']} — these are incompatible."
                        )
            break

    # 2. Suspicious quantity
    if action in ("confirm", "clarify"):
        for item in items_list:
            if item.get("item_name") and item.get("quantity"):
                if _detect_suspicious_quantity(item["item_name"], item["quantity"], db):
                    extra_flags.append("suspicious_quantity")
                    logger.warning(
                        "validate  SUSPICIOUS QTY  item=%r  qty=%s  unit=%r",
                        item["item_name"], item["quantity"], item.get("unit"),
                    )
                    warn_texts.append(
                        f"⚠ {item['quantity']} {item.get('unit', '')} of "
                        f"{item['item_name']} seems unusually large."
                    )
            break

    # 3. Storage appropriateness
    if action in ("confirm", "clarify"):
        for item in items_list:
            storage_warn = _check_storage_appropriateness(
                item.get("item_name", ""),
                item.get("category", ""),
                workspace_storage,
            )
            if storage_warn:
                extra_flags.append("storage_warning")
                logger.warning(
                    "validate  STORAGE MISMATCH  item=%r  cat=%r  area=%r  warn=%r",
                    item.get("item_name"), item.get("category"), workspace_storage,
                    storage_warn[:80],
                )
                if not _aria_has_storage_warn:
                    warn_texts.append(storage_warn)
            break

    # 4. Same-day conflict
    if action in ("confirm", "clarify"):
        for item in items_list:
            if item.get("item_name") and item.get("quantity") and item.get("storage_area"):
                conflict_msg = _detect_same_day_conflict(
                    item["item_name"],
                    item["storage_area"],
                    worker_id,
                    item["quantity"],
                    db,
                )
                if conflict_msg:
                    extra_flags.append("conflict")
                    logger.warning(
                        "validate  SAME-DAY CONFLICT  item=%r  area=%r  msg=%r",
                        item["item_name"], item.get("storage_area"), conflict_msg[:80],
                    )
                    warn_texts.append(f"⚠ {conflict_msg}.")
            break

    # ── Apply collected warnings ─────────────────────────────────────────────
    if warn_texts and action in ("confirm", "clarify"):
        if action == "clarify":
            # ARIA already had a clarification question (unit switch, fuzzy unit, etc.)
            # Prepend it so the worker sees both ARIA's question AND our warnings.
            aria_question = message.split("\n⚠")[0].rstrip()
            parts: list[str] = [aria_question] + warn_texts
        else:
            # ARIA returned confirm ("Adding X to shelf...") — drop that message
            # entirely; jump straight to the warnings without the action prefix.
            parts = warn_texts
        message = "\n\n".join(parts) + "\n\nSay yes to proceed anyway, or no to cancel."
        action = "clarify"

    # ── Store pending for confirm AND clarify-with-items ──────────────────────
    #
    # Storing for clarify is the key fix for the "yes after clarify stores" flow:
    # when ARIA clarifies an interpretation ("Did you mean X?") or we downgrade
    # confirm→clarify above, the items are already known.  Storing here means
    # the next "yes" finds a pending action → is_affirmation=True → execute fires.
    # Without this, "yes" after clarify leaves is_affirmation=False (no pending
    # existed at turn start) and ARIA returns confirm again, requiring a second yes.
    _pending_items_with_qty = [
        i for i in items_list
        if i.get("item_name") and i.get("quantity") is not None
    ]
    if action in ("confirm", "clarify") and _pending_items_with_qty:
        if workspace_storage:
            for item in data.get("items", []):
                if isinstance(item, dict):
                    item["storage_area"] = workspace_storage
        _pending_actions[session_id] = data
        logger.info(
            "validate  PENDING STORED  session=%s  items=%d  area=%r",
            session_id, len(_pending_items_with_qty), workspace_storage,
        )

    logger.info(
        "NODE ◀── validate  action=%s  extra_flags=%s  warns=%d  pending_stored=%s",
        action, extra_flags, len(warn_texts), bool(_pending_items_with_qty and action in ("confirm", "clarify")),
    )

    return {
        "aria_result": {**aria_result, "action": action, "message": message, "data": data},
        "extra_flags": extra_flags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# execute_node  — DB writes + conversation persistence
# ─────────────────────────────────────────────────────────────────────────────

def execute_node(state: WorkflowState) -> dict:
    """
    Execute inventory DB writes and persist the conversation turn.

    Three execution paths:
      1. ARIA returned action='update' + confirmed=True → direct execute
      2. ARIA intent='confirm' with action='none' → execute from pending store
      3. Python-level affirmation override → force execute when ARIA missed it
    """
    from services.ai_service import (
        _execute_inventory_updates,
        _pending_actions,
        _save_user_insights,
    )
    from models import ConversationMessage

    text = state["text"]
    session_id = state["session_id"]
    worker_id = state["worker_id"]
    storage_area = state.get("storage_area") or ""
    location_name = state.get("location_name") or ""
    db = state["db"]
    is_affirmation = state.get("is_affirmation", False)
    aria_result = state.get("aria_result") or {}
    extra_flags_from_validate = list(state.get("extra_flags") or [])

    action = aria_result.get("action", "none")
    data = dict(aria_result.get("data") or {})
    message = aria_result.get("message", "")
    intent = aria_result.get("intent", "none")
    inventory_updated = False

    logger.info(
        "NODE ──▶ execute  session=%s  worker=%s  action=%s  intent=%s  "
        "is_affirmation=%s  items=%d",
        session_id, worker_id, action, intent, is_affirmation,
        len(data.get("items", [])),
    )

    # Path 1: ARIA confirmed
    if action == "update" and data.get("confirmed"):
        pending = _pending_actions.get(session_id, data)
        pending_items = pending.get("items", [])
        logger.info(
            "execute  PATH=1 (ARIA action=update + confirmed=True)  "
            "session=%s  pending_items=%d",
            session_id, len(pending_items),
        )
        inventory_updated = _execute_inventory_updates(
            pending, worker_id, db,
            workspace_location=location_name,
            workspace_storage=storage_area,
        )
        _pending_actions.pop(session_id, None)
        logger.info("execute  Path1 result  inventory_updated=%s", inventory_updated)

    # Path 2: ARIA returned intent='confirm' with action='none'
    if action == "none" and intent == "confirm":
        pending = _pending_actions.get(session_id)
        if pending:
            logger.info(
                "execute  PATH=2 (intent=confirm + action=none + pending exists)  [EDGE CASE]  session=%s",
                session_id,
            )
            inventory_updated = _execute_inventory_updates(
                pending, worker_id, db,
                workspace_location=location_name,
                workspace_storage=storage_area,
            )
            _pending_actions.pop(session_id, None)
            logger.info("execute  Path2 result  inventory_updated=%s", inventory_updated)
        else:
            logger.warning(
                "execute  PATH=2 (intent=confirm) but NO pending action  [EDGE CASE]  "
                "session=%s — nothing to execute", session_id,
            )

    # Path 3: Python-level affirmation override (ARIA missed the confirmation)
    if is_affirmation and not inventory_updated and session_id in _pending_actions:
        logger.warning(
            "execute  PATH=3 (affirmation override — ARIA missed it)  [EDGE CASE]  session=%s",
            session_id,
        )
        pending = _pending_actions.pop(session_id)
        inventory_updated = _execute_inventory_updates(
            pending, worker_id, db,
            workspace_location=location_name,
            workspace_storage=storage_area,
        )
        if inventory_updated and action not in ("update",):
            message = f"Done! Added to {storage_area or 'storage'}."
            action = "update"
        logger.info("execute  Path3 result  inventory_updated=%s", inventory_updated)

    # Clear pending on explicit denial
    if intent == "deny":
        had_pending = session_id in _pending_actions
        _pending_actions.pop(session_id, None)
        logger.info(
            "execute  PENDING CLEARED (deny)  session=%s  had_pending=%s",
            session_id, had_pending,
        )

    # Persist conversation turn
    db.add(ConversationMessage(session_id=session_id, role="user", content=text))
    db.add(ConversationMessage(
        session_id=session_id,
        role="assistant",
        content=message,
        action_taken=action if action != "none" else None,
    ))
    db.commit()
    logger.info(
        "  DB CONV SAVED  session=%s  user=%r  assistant=%r  action_taken=%s",
        session_id, text[:60], message[:80], action if action != "none" else None,
    )

    # Keep DB user insights in sync (backward compat with existing UserProfile table)
    try:
        _save_user_insights(worker_id, aria_result, db)
    except Exception as exc:
        logger.warning("execute_node  _save_user_insights failed: %s", exc)

    # Merge and deduplicate all flags
    all_flags = list(data.get("flags") or []) + extra_flags_from_validate
    data["flags"] = list(dict.fromkeys(all_flags))

    logger.info(
        "NODE ◀── execute  action=%s  intent=%s  inventory_updated=%s  flags=%s",
        action, intent, inventory_updated, data["flags"],
    )

    return {
        "message": message,
        "action": action,
        "intent": intent,
        "data": data,
        "inventory_updated": inventory_updated,
    }


# ─────────────────────────────────────────────────────────────────────────────
# persist_memory_node  — Mem0 writeback
# ─────────────────────────────────────────────────────────────────────────────

async def persist_memory_node(state: WorkflowState) -> dict:
    """
    Persist worker behavioural insights to Mem0 after each interaction.

    Written as natural language statements so Mem0's semantic deduplication
    can automatically update related memories on subsequent turns:
      - tone / communication preference (derived from emotion)
      - personality observations
      - custom vocabulary entries (lexicons)
      - item patterns (what the worker manages)

    This is additive to the DB writes in execute_node — Mem0 becomes the
    cross-session long-term memory while the DB keeps the authoritative
    short-term inventory state.
    """
    worker_id = state["worker_id"]
    aria_result = state.get("aria_result") or {}
    action = state.get("action", "none")
    data = state.get("data") or {}
    storage_area = state.get("storage_area") or "General Storage"

    user_emotion = aria_result.get("user_emotion", "neutral") or "neutral"
    personality_note = (aria_result.get("personality_note") or "").strip()
    new_lexicons = aria_result.get("new_lexicons") or []

    statements: list[str] = []

    logger.info(
        "NODE ──▶ persist_memory  worker=%s  emotion=%s  action=%s  "
        "personality_note=%r  new_lexicons=%d",
        worker_id, user_emotion, action,
        personality_note[:60] if personality_note else None,
        len(new_lexicons),
    )

    # Tone preference inferred from emotion
    if user_emotion in ("frustrated", "angry"):
        statements.append(
            f"Worker {worker_id} prefers formal communication when stressed."
        )
    elif user_emotion in ("happy", "excited"):
        statements.append(
            f"Worker {worker_id} is enthusiastic and responds well to upbeat tone."
        )
    elif user_emotion == "neutral":
        statements.append(
            f"Worker {worker_id} prefers friendly, efficient communication."
        )

    # Personality observation
    if personality_note:
        statements.append(f"Personality observation about {worker_id}: {personality_note}")

    # Custom lexicons
    for lex in new_lexicons:
        original = (lex.get("original_word") or "").strip()
        resolved = (lex.get("resolved_word") or "").strip()
        word_type = lex.get("word_type", "custom")
        if original:
            if resolved:
                statements.append(
                    f"Worker {worker_id}'s vocabulary: '{original}' means '{resolved}' ({word_type})."
                )
            else:
                statements.append(
                    f"Worker {worker_id} uses the term '{original}' ({word_type})."
                )

    # Item management patterns (recorded when inventory is actually updated)
    if action in ("update", "confirm"):
        items_list = data.get("items") or []
        if not items_list and data.get("item_name"):
            items_list = [data]
        for item in items_list[:2]:  # cap at 2 per turn to keep memory concise
            item_name = (item.get("item_name") or "").strip()
            if item_name:
                statements.append(
                    f"Worker {worker_id} manages {item_name} inventory in {storage_area}."
                )

    if statements:
        combined = " ".join(statements)
        logger.info("  MEM0 WRITE  worker=%s  statements=%d  content=%r", worker_id, len(statements), combined[:200])
        await add_user_memory(combined, worker_id)
        logger.info("NODE ◀── persist_memory  mem0_written=True  statements=%d", len(statements))
    else:
        logger.info("NODE ◀── persist_memory  mem0_written=False  (no statements)")

    return {}  # pure side-effect node — no state mutations
