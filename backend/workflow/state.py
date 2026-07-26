"""
LangGraph state for the ARIA voice-inventory workflow.

All fields are optional (total=False) so nodes can update them
incrementally without needing to supply every key on every return.

The extra_flags field uses operator.add as its reducer so multiple
nodes can append flags without overwriting each other.
"""

import operator
from typing import Any, Dict, List, Optional
from typing_extensions import Annotated, TypedDict


class WorkflowState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────────────────────
    text: str
    session_id: str
    worker_id: str
    storage_area: str
    location_name: str
    db: Any  # SQLAlchemy Session — not serialised (no checkpointer used)

    # ── Context loaded in parallel ────────────────────────────────────────────
    # load_context_node writes these
    inventory_context: str
    inventory_summary: Dict         # Python-computed totals for the analytics fast-path
    item_history_context: str
    conversation_history: List[Dict]
    pending_action: Optional[Dict]  # DB-persisted pending confirmation state
    rejection_context: str          # items guard rejected this session
    session_digest: str             # compressed episodic summary of older turns
    used_db_history: bool           # True when history came from load_compressed_history
                                     # (client-provided history means any digest we write
                                     # would never be read back — see persist_memory_node)

    # load_memory_node writes these
    user_memories: List[Dict]       # raw Mem0 records
    user_profile_context: str       # formatted string for ARIA prompt

    # ── Preprocessing ─────────────────────────────────────────────────────────
    fuzzy_units_hint: str
    fragment_hint: str
    is_affirmation: bool

    # ── Intent classification (runs before guard) ─────────────────────────────
    intent_result: Dict             # full IntentResult dict
    pre_classified_intent: str      # intent label injected into ARIA prompt
    intent_confidence: float
    needs_clarification: bool       # True → short-circuit to clarify_node

    # ── Guard ─────────────────────────────────────────────────────────────────
    guard_result: Dict
    guard_rejected: bool

    # ── Extraction agent output ───────────────────────────────────────────────
    extraction_result: Dict  # ExtractionResult dict

    # ── ARIA agent output ─────────────────────────────────────────────────────
    aria_result: Dict

    # ── Validation flags (operator.add lets multiple nodes accumulate flags) ──
    extra_flags: Annotated[List[str], operator.add]

    # ── Execution ─────────────────────────────────────────────────────────────
    inventory_updated: bool

    # ── Priority agent output (context pruning — sits between extraction and aria)
    priority_inventory_context: str
    priority_item_history_context: str
    priority_conversation_history: List[Dict]
    priority_profile_context: str
    priority_focus: str              # human-readable label: what the agent is focused on

    # ── Final response ────────────────────────────────────────────────────────
    message: str
    action: str
    intent: str
    data: Dict
