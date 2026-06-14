#!/usr/bin/env python3
"""
ARIA prompt eval & grader.

Calls aria_process() directly — no server or DB required.
All context is synthetic but matches what the workflow nodes produce exactly.

Usage (from backend/):
    python -m eval.run_eval
    python -m eval.run_eval --tags happy_path fragment
    python -m eval.run_eval --ids simple_add storage_mismatch_beef_dry
    python -m eval.run_eval --output eval/results/latest.json
    python -m eval.run_eval --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── Path & env setup ─────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

_env_path = _BACKEND / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from agents.aria_agent import aria_process  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
TODAY = str(date.today())
NEXT_YEAR = str(date.today().replace(year=date.today().year + 1))

FILLER_PHRASES = [
    "let me know if there",
    "anything else you need",
    "anything else i can",
    "feel free to ask",
    "don't hesitate",
    "is there anything else",
    "happy to help",
    "how else can i",
    "is there anything i can",
]


# ── Context builders (mirror exactly what workflow nodes produce) ──────────────

def _workspace_ctx(location: str, area: str) -> str:
    return (
        f"Location: {location}\n"
        f"Storage Area: {area}\n"
        f"IMPORTANT: These values are FIXED by the UI. Always use '{area}' "
        f"as storage_area in every item, regardless of what the worker says in speech."
    )


def _inventory_ctx(items: list[str], location: str, area: str, total: float = 0.0) -> str:
    lines = [f"## Current Inventory ({location} - {area})"]
    for item in items:
        lines.append(f"  • {item}")
    lines += [
        "",
        "## Pre-computed workspace summary",
        f"Workspace total ({location} {area}): ${total:.2f}",
        f"Grand total across ALL locations: ${total:.2f}",
    ]
    return "\n".join(lines)


def _conv_json(turns: list[tuple[str, str]]) -> str:
    msgs = [{"role": r, "content": c} for r, c in turns]
    return json.dumps(msgs, indent=2)


def _pending_ctx(
    items: list[dict] | None = None,
    is_affirmation: bool = False,
    text: str = "yes",
) -> str:
    if not items:
        return "No pending action."
    data = {"items": items, "confirmed": False}
    if is_affirmation:
        return (
            f"⚡ AFFIRMATION DETECTED — worker said '{text}' which means YES/CONFIRMED.\n"
            f"Execute this NOW: action='update', confirmed=true.\n"
            f"Pending:\n{json.dumps(data, indent=2)}"
        )
    return (
        f"Pending confirmation for this session:\n{json.dumps(data, indent=2)}\n"
        "If the worker says yes/confirm/proceed/that's correct, "
        "execute this (action='update', confirmed=true)."
    )


def _extraction_ctx(items: list[dict]) -> str:
    if not items:
        return "No structured extraction available for this message."
    lines = [
        "## Pre-extracted Inventory Items (strict extraction — trust these over raw speech)"
    ]
    for it in items:
        lines.append(
            f"  • {it['name']}: qty={it.get('qty')}, unit={it.get('unit', 'kg')}, "
            f"category={it.get('cat', 'miscellaneous')}, catalog=UNKNOWN, "
            f"confidence=HIGH, requires_confirmation=True"
        )
    lines.append("  Overall extraction confidence: HIGH")
    return "\n".join(lines)


def _count_sentences(text: str) -> int:
    """Count sentences; masks decimals and prices so they don't split."""
    t = re.sub(r'\$?\d+\.\d+', 'NUM', text)
    parts = re.split(r'(?<=[.!?])\s+', t.strip())
    return len([p for p in parts if p.strip()])


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    id: str
    description: str
    rule: str
    tags: list[str] = field(default_factory=list)

    # aria_process() inputs
    text: str = ""
    pre_classified_intent: str = "add"
    intent_slots: str = ""
    workspace_area: str = "Fridge"
    workspace_location: str = "New York"
    inventory_items: list[str] = field(default_factory=list)
    inventory_total: float = 0.0
    conversation_history_turns: list[tuple[str, str]] = field(default_factory=list)
    pending_items: list[dict] | None = None
    is_affirmation: bool = False
    affirmation_text: str = "yes"
    fuzzy_units_hint: str = ""
    extraction_items: list[dict] = field(default_factory=list)
    item_history: str = ""

    # Expected outputs
    expected_action: str | None = None
    expected_intent: str | None = None
    # Each entry: {item_name?, quantity?, unit?, operation?, storage_area?}
    expected_items: list[dict] = field(default_factory=list)
    expected_confirmed: bool | None = None
    flags_must_include: list[str] = field(default_factory=list)
    flags_must_not_include: list[str] = field(default_factory=list)
    message_must_contain: list[str] = field(default_factory=list)
    message_must_not_contain: list[str] = field(default_factory=list)
    check_no_filler: bool = True
    check_storage_locked: bool = True
    check_unit_price_positive: bool = False
    max_sentences: int = 3


@dataclass
class CheckResult:
    name: str
    passed: bool
    expected: str
    actual: str
    weight: int


@dataclass
class GradeResult:
    test_id: str
    description: str
    rule: str
    passed: bool       # True iff all weight≥2 checks pass
    score: float       # 0.0–1.0 weighted score
    checks: list[CheckResult]
    aria_result: dict
    elapsed_ms: float
    error: str | None = None


# ── Test cases ────────────────────────────────────────────────────────────────

_FRIDGE_ITEMS = [
    "Chicken Breast: 10 kg ($8.50/kg) — Added today",
    "Milk: 5 litres ($1.20/litre) — Added today",
    "Tomatoes: 3 kg ($2.00/kg) — Added yesterday",
]
_FRIDGE_TOTAL = 10 * 8.50 + 5 * 1.20 + 3 * 2.00   # 97.00


TEST_CASES: list[TestCase] = [

    # ═══════════════════════════════════════════════════════════════════════════
    # Happy path
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="simple_add",
        description="Add 5 kg of tomatoes → confirm, item/qty/unit correct",
        rule="General",
        tags=["happy_path"],
        text="Add 5 kg of tomatoes",
        pre_classified_intent="add",
        intent_slots="item='tomatoes', quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "tomatoes", "qty": 5.0, "unit": "kg", "cat": "vegetable"}],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[{"item_name": "tomatoes", "quantity": 5.0, "unit": "kg", "operation": "add"}],
        check_unit_price_positive=True,
        max_sentences=2,
    ),
    TestCase(
        id="simple_remove",
        description="Remove 2 kg of beef → confirm, operation=subtract",
        rule="General",
        tags=["happy_path"],
        text="Remove 2 kg of beef",
        pre_classified_intent="remove",
        intent_slots="item='beef', quantity=2.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "beef", "qty": 2.0, "unit": "kg", "cat": "meat"}],
        expected_action="confirm",
        expected_intent="remove",
        expected_items=[{"item_name": "beef", "quantity": 2.0, "unit": "kg", "operation": "subtract"}],
        max_sentences=2,
    ),
    TestCase(
        id="simple_set",
        description="Set chicken to 8 kg → confirm, operation=set",
        rule="General",
        tags=["happy_path"],
        text="Set chicken to 8 kg",
        pre_classified_intent="set",
        intent_slots="item='chicken', quantity=8.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "chicken", "qty": 8.0, "unit": "kg", "cat": "meat"}],
        expected_action="confirm",
        expected_intent="set",
        expected_items=[{"item_name": "chicken", "quantity": 8.0, "unit": "kg", "operation": "set"}],
        max_sentences=2,
    ),
    TestCase(
        id="affirmation_execute",
        description="⚡ AFFIRMATION DETECTED with pending → action=update, confirmed=True",
        rule="General Rule #3",
        tags=["happy_path", "affirmation"],
        text="yes",
        pre_classified_intent="confirm",
        intent_slots="",
        workspace_area="Fridge",
        pending_items=[
            {"item_name": "tomatoes", "quantity": 5.0, "unit": "kg",
             "storage_area": "Fridge", "operation": "add", "category": "Vegetables"}
        ],
        is_affirmation=True,
        affirmation_text="yes",
        expected_action="update",
        expected_intent="confirm",
        expected_confirmed=True,
        check_storage_locked=False,   # short-circuit: items from pending, not re-extracted
        max_sentences=2,
    ),
    TestCase(
        id="multi_item_add",
        description="Two items in one command → both appear in items[]",
        rule="General Rule #5",
        tags=["happy_path", "multi_item"],
        text="Add 5 kg of tomatoes and 3 litres of milk",
        pre_classified_intent="add",
        intent_slots="",
        workspace_area="Fridge",
        extraction_items=[
            {"name": "tomatoes", "qty": 5.0, "unit": "kg",    "cat": "vegetable"},
            {"name": "milk",     "qty": 3.0, "unit": "litre", "cat": "dairy"},
        ],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[
            {"item_name": "tomatoes", "quantity": 5.0},
            {"item_name": "milk",     "quantity": 3.0},
        ],
        max_sentences=2,
    ),
    TestCase(
        id="expiry_date_captured",
        description=f"Add 5 kg salmon expires {NEXT_YEAR} → item.expiry_date populated",
        rule="Schema",
        tags=["happy_path", "expiry"],
        text=f"Add 5 kg of salmon, expires {NEXT_YEAR}",
        pre_classified_intent="add",
        intent_slots=f"item='salmon', quantity=5.0, unit='kg'",
        workspace_area="Freezer",
        extraction_items=[{"name": "salmon", "qty": 5.0, "unit": "kg", "cat": "seafood"}],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[{"item_name": "salmon", "quantity": 5.0}],
        max_sentences=2,
    ),
    TestCase(
        id="unit_price_always_positive",
        description="Items must always carry unit_price > 0",
        rule="Rule 10",
        tags=["happy_path", "pricing"],
        text="Add 2 kg of salmon",
        pre_classified_intent="add",
        intent_slots="item='salmon', quantity=2.0, unit='kg'",
        workspace_area="Freezer",
        extraction_items=[{"name": "salmon", "qty": 2.0, "unit": "kg", "cat": "seafood"}],
        expected_action="confirm",
        check_unit_price_positive=True,
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 0a — Denial vs correction
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="denial_cancel",
        description="'No, cancel that' with pending → action=none, intent=deny",
        rule="Rule 0a",
        tags=["denial"],
        text="No, cancel that",
        pre_classified_intent="deny",
        intent_slots="",
        workspace_area="Fridge",
        pending_items=[
            {"item_name": "beef", "quantity": 2.0, "unit": "kg",
             "storage_area": "Fridge", "operation": "add"}
        ],
        expected_action="none",
        expected_intent="deny",
        check_storage_locked=False,
        max_sentences=2,
    ),
    TestCase(
        id="correction_not_denial",
        description="'no I meant 3 kg not 5 kg' → confirm 3 kg, NOT deny",
        rule="Rule 0a",
        tags=["denial", "correction"],
        text="no I meant 3 kg not 5 kg",
        pre_classified_intent="add",
        intent_slots="item='tomatoes', quantity=3.0, unit='kg'",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "Add 5 kg of tomatoes"),
            ("assistant", "I'll add 5 kg of tomatoes to the Fridge. Confirm?"),
        ],
        extraction_items=[{"name": "tomatoes", "qty": 3.0, "unit": "kg", "cat": "vegetable"}],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[{"quantity": 3.0}],
        max_sentences=2,
    ),
    TestCase(
        id="unit_affirmation_not_denial",
        description="'no liters is fine' (unit confirmation) → should NOT deny",
        rule="Rule 0a",
        tags=["denial", "unit"],
        text="no liters is fine",
        pre_classified_intent="confirm",
        intent_slots="",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "Add 3 litres of olive oil"),
            ("assistant", "Olive oil can't be in litres — did you mean kg?"),
        ],
        pending_items=[
            {"item_name": "olive oil", "quantity": 3.0, "unit": "litre",
             "storage_area": "Fridge", "operation": "add"}
        ],
        # worker is saying "no [to your suggestion], liters is fine"
        # → must NOT be deny/none
        expected_action="update",
        expected_intent="confirm",
        expected_confirmed=True,
        check_storage_locked=False,
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 0b — Fragment completion
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="fragment_qty_no_context",
        description="'5 kg' with no conversation history → clarify (EXCEPTION)",
        rule="Rule 0b CASE B EXCEPTION",
        tags=["fragment", "edge_case"],
        text="5 kg",
        pre_classified_intent="add",
        intent_slots="quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        expected_action="clarify",
        check_storage_locked=False,
        max_sentences=2,
    ),
    TestCase(
        id="fragment_qty_with_active_item",
        description="'5 kg' after worker said 'add tomatoes' → inherit → confirm (CASE B)",
        rule="Rule 0b CASE B",
        tags=["fragment", "edge_case"],
        text="5 kg",
        pre_classified_intent="add",
        intent_slots="quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "add tomatoes"),
            ("assistant", "How many kg of tomatoes would you like to add?"),
        ],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[{"item_name": "tomatoes", "quantity": 5.0, "unit": "kg"}],
        max_sentences=2,
    ),
    TestCase(
        id="fragment_item_after_qty_turn",
        description="'tomatoes' after user said '5 kg' → combine → 5 kg tomatoes (CASE A)",
        rule="Rule 0b CASE A",
        tags=["fragment", "edge_case"],
        text="tomatoes",
        pre_classified_intent="add",
        intent_slots="item='tomatoes'",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "5 kg"),
            ("assistant", "5 kg of what?"),
        ],
        expected_action="confirm",
        expected_intent="add",
        expected_items=[{"item_name": "tomatoes", "quantity": 5.0, "unit": "kg"}],
        max_sentences=2,
    ),
    TestCase(
        id="fragment_qty_after_cancelled_turn",
        description="'5 kg' where only prior item was DENIED → clarify (EXCEPTION)",
        rule="Rule 0b CASE B EXCEPTION",
        tags=["fragment", "edge_case", "denial"],
        text="5 kg",
        pre_classified_intent="add",
        intent_slots="quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "Add 3 kg of beef"),
            ("assistant", "I'll add 3 kg of beef. Confirm?"),
            ("user",      "No, cancel that"),
            ("assistant", "Cancelled. No changes made."),
        ],
        expected_action="clarify",
        check_storage_locked=False,
        max_sentences=2,
    ),
    TestCase(
        id="fragment_qty_after_query_answer",
        description="'5 kg' after ARIA listed items in a query answer → clarify (EXCEPTION)",
        rule="Rule 0b CASE B EXCEPTION",
        tags=["fragment", "edge_case"],
        text="5 kg",
        pre_classified_intent="add",
        intent_slots="quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        conversation_history_turns=[
            ("user",      "What's in the fridge?"),
            ("assistant", "You have chicken breast (10 kg) and milk (5 litres) in the Fridge."),
        ],
        expected_action="clarify",
        check_storage_locked=False,
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 1 — Unit auto-correction
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="unit_auto_correct_solid_in_litres",
        description="'5 litres of chicken' → auto-correct to kg, action=update, unit_changed flag",
        rule="Rule 1",
        tags=["unit_correction"],
        text="Add 5 litres of chicken",
        pre_classified_intent="add",
        intent_slots="item='chicken', quantity=5.0, unit='litres'",
        workspace_area="Fridge",
        extraction_items=[{"name": "chicken", "qty": 5.0, "unit": "litre", "cat": "meat"}],
        expected_action="update",
        expected_intent="add",
        expected_items=[{"item_name": "chicken", "quantity": 5.0, "unit": "kg", "operation": "add"}],
        expected_confirmed=True,
        flags_must_include=["unit_changed"],
        max_sentences=2,
    ),
    TestCase(
        id="unit_auto_correct_liquid_in_kg",
        description="'5 kg of milk' (liquid) → auto-correct to litre, action=update",
        rule="Rule 1",
        tags=["unit_correction"],
        text="Add 5 kg of milk",
        pre_classified_intent="add",
        intent_slots="item='milk', quantity=5.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "milk", "qty": 5.0, "unit": "kg", "cat": "dairy"}],
        expected_action="update",
        expected_intent="add",
        expected_items=[{"item_name": "milk", "quantity": 5.0, "unit": "litre", "operation": "add"}],
        expected_confirmed=True,
        flags_must_include=["unit_changed"],
        max_sentences=2,
    ),
    TestCase(
        id="unit_compatible_grams_to_kg",
        description="'500 g of sugar' (same group: g ↔ kg) → confirm, NOT auto-execute",
        rule="Rule 1",
        tags=["unit_correction"],
        text="Add 500 g of sugar",
        pre_classified_intent="add",
        intent_slots="item='sugar', quantity=500.0, unit='g'",
        workspace_area="Dry Storage",
        extraction_items=[{"name": "sugar", "qty": 500.0, "unit": "g", "cat": "grain"}],
        expected_action="confirm",
        expected_intent="add",
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 3 — Storage area is fixed
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="storage_area_locked_to_workspace",
        description="Worker says 'put in freezer' but workspace=Fridge → item.storage_area=Fridge",
        rule="Rule 3",
        tags=["storage"],
        text="Add 2 kg of tomatoes and put them in the freezer",
        pre_classified_intent="add",
        intent_slots="item='tomatoes', quantity=2.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "tomatoes", "qty": 2.0, "unit": "kg", "cat": "vegetable"}],
        expected_action="confirm",
        expected_items=[{"item_name": "tomatoes", "quantity": 2.0, "storage_area": "Fridge"}],
        check_storage_locked=True,
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 4 — Storage area intelligence (warnings)
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="storage_mismatch_beef_in_dry",
        description="Raw beef in Dry Storage → still confirm, but ⚠ in message",
        rule="Rule 4",
        tags=["storage", "warning"],
        text="Add 5 kg of beef",
        pre_classified_intent="add",
        intent_slots="item='beef', quantity=5.0, unit='kg'",
        workspace_area="Dry Storage",
        extraction_items=[{"name": "beef", "qty": 5.0, "unit": "kg", "cat": "meat"}],
        expected_action="confirm",
        expected_items=[{"item_name": "beef", "quantity": 5.0}],
        message_must_contain=["⚠"],
        max_sentences=4,
    ),
    TestCase(
        id="storage_mismatch_milk_in_freezer",
        description="Milk in Freezer → warn (dairy → Fridge, not Freezer)",
        rule="Rule 4",
        tags=["storage", "warning"],
        text="Add 5 litres of milk",
        pre_classified_intent="add",
        intent_slots="item='milk', quantity=5.0, unit='litres'",
        workspace_area="Freezer",
        extraction_items=[{"name": "milk", "qty": 5.0, "unit": "litre", "cat": "dairy"}],
        expected_action="confirm",
        expected_items=[{"item_name": "milk"}],
        message_must_contain=["⚠"],
        max_sentences=4,
    ),
    TestCase(
        id="no_false_storage_warning_tomatoes_fridge",
        description="Tomatoes in Fridge is fine → no ⚠ warning",
        rule="Rule 4",
        tags=["storage"],
        text="Add 3 kg of tomatoes",
        pre_classified_intent="add",
        intent_slots="item='tomatoes', quantity=3.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "tomatoes", "qty": 3.0, "unit": "kg", "cat": "vegetable"}],
        expected_action="confirm",
        message_must_not_contain=["⚠"],
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 7 — Total value queries
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="total_value_unscoped_uses_workspace",
        description="Unscoped 'total value' → workspace total figure (Rule 7A)",
        rule="Rule 7A",
        tags=["analytics"],
        text="What is the total value of our inventory?",
        pre_classified_intent="analytics",
        intent_slots="",
        workspace_area="Fridge",
        workspace_location="New York",
        inventory_items=_FRIDGE_ITEMS,
        inventory_total=_FRIDGE_TOTAL,
        expected_action="query_result",
        expected_intent="analytics",
        message_must_contain=["$", "97"],   # must cite the pre-computed $97.00
        check_storage_locked=False,
        max_sentences=2,
    ),
    TestCase(
        id="total_value_grand_total_all_locations",
        description="'grand total across all locations' → uses all-locations figure (Rule 7B)",
        rule="Rule 7B",
        tags=["analytics"],
        text="What's the grand total across all our locations?",
        pre_classified_intent="analytics",
        intent_slots="",
        workspace_area="Fridge",
        workspace_location="New York",
        inventory_items=_FRIDGE_ITEMS,
        inventory_total=_FRIDGE_TOTAL,
        expected_action="query_result",
        expected_intent="analytics",
        message_must_contain=["$"],
        check_storage_locked=False,
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 9 — Item name grounding
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="item_name_not_paraphrased",
        description="'chicken breast' stays 'chicken breast', never renamed to 'poultry'",
        rule="Rule 9",
        tags=["grounding"],
        text="Add 3 kg of chicken breast",
        pre_classified_intent="add",
        intent_slots="item='chicken breast', quantity=3.0, unit='kg'",
        workspace_area="Fridge",
        extraction_items=[{"name": "chicken breast", "qty": 3.0, "unit": "kg", "cat": "meat"}],
        expected_action="confirm",
        expected_items=[{"item_name": "chicken breast"}],
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 2 — Voice/abbreviated units
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="voice_unit_lit_is_valid",
        description="'1 lit of olive oil' → treated as litre, no unit_mismatch flag",
        rule="Rule 2",
        tags=["unit", "voice"],
        text="Add 1 lit of olive oil",
        pre_classified_intent="add",
        intent_slots="item='olive oil', quantity=1.0, unit='lit'",
        workspace_area="Dry Storage",
        extraction_items=[{"name": "olive oil", "qty": 1.0, "unit": "litre", "cat": "oil"}],
        expected_action="confirm",
        expected_intent="add",
        flags_must_not_include=["unit_mismatch"],
        max_sentences=2,
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # General Rule #2 — Response quality
    # ═══════════════════════════════════════════════════════════════════════════
    TestCase(
        id="no_filler_on_confirm",
        description="Simple confirm must not end with filler like 'Let me know if…'",
        rule="General Rule #2 (no filler)",
        tags=["response_quality", "filler"],
        text="Add 3 kg of onions",
        pre_classified_intent="add",
        intent_slots="item='onions', quantity=3.0, unit='kg'",
        workspace_area="Dry Storage",
        extraction_items=[{"name": "onions", "qty": 3.0, "unit": "kg", "cat": "vegetable"}],
        expected_action="confirm",
        check_no_filler=True,
        max_sentences=2,
    ),
    TestCase(
        id="response_max_two_sentences",
        description="Simple add must produce ≤2 sentences",
        rule="General Rule #2 (brevity)",
        tags=["response_quality"],
        text="Add 5 kg of flour",
        pre_classified_intent="add",
        intent_slots="item='flour', quantity=5.0, unit='kg'",
        workspace_area="Dry Storage",
        extraction_items=[{"name": "flour", "qty": 5.0, "unit": "kg", "cat": "grain"}],
        expected_action="confirm",
        check_no_filler=True,
        max_sentences=2,
    ),
    TestCase(
        id="no_filler_on_query_result",
        description="Query answer must not end with filler",
        rule="General Rule #2 (no filler)",
        tags=["response_quality", "filler"],
        text="How much chicken do we have?",
        pre_classified_intent="query",
        intent_slots="item='chicken'",
        workspace_area="Fridge",
        inventory_items=_FRIDGE_ITEMS,
        expected_action="query_result",
        check_no_filler=True,
        check_storage_locked=False,
        max_sentences=2,
    ),
]


# ── Grader ────────────────────────────────────────────────────────────────────

def grade(tc: TestCase, result: dict) -> GradeResult:
    checks: list[CheckResult] = []

    def chk(name: str, passed: bool, expected, actual, weight: int = 1) -> None:
        checks.append(CheckResult(
            name=name, passed=passed,
            expected=str(expected), actual=str(actual),
            weight=weight,
        ))

    data      = result.get("data", {}) or {}
    items     = data.get("items", []) or []
    message   = result.get("message", "") or ""
    action    = result.get("action", "")
    intent    = result.get("intent", "")
    confirmed = data.get("confirmed", False)
    flags     = data.get("flags", []) or []

    # 1. Action (critical)
    if tc.expected_action is not None:
        chk("action", action == tc.expected_action, tc.expected_action, action, weight=3)

    # 2. Intent
    if tc.expected_intent is not None:
        chk("intent", intent == tc.expected_intent, tc.expected_intent, intent, weight=2)

    # 3. Item checks
    for i, exp in enumerate(tc.expected_items):
        if i >= len(items):
            chk(f"item[{i}]_exists", False, f"item[{i}] present", "missing", weight=2)
            continue
        actual_item = items[i]

        if "item_name" in exp:
            exp_n = exp["item_name"].lower()
            act_n = (actual_item.get("item_name") or "").lower()
            chk(f"item[{i}].name", exp_n in act_n or act_n in exp_n,
                exp["item_name"], actual_item.get("item_name"), weight=2)

        if "quantity" in exp:
            chk(f"item[{i}].quantity",
                actual_item.get("quantity") == exp["quantity"],
                exp["quantity"], actual_item.get("quantity"), weight=2)

        if "unit" in exp:
            act_u = (actual_item.get("unit") or "").lower().rstrip("s")
            exp_u = exp["unit"].lower().rstrip("s")
            chk(f"item[{i}].unit", act_u == exp_u, exp["unit"], actual_item.get("unit"), weight=1)

        if "operation" in exp:
            chk(f"item[{i}].operation",
                actual_item.get("operation") == exp["operation"],
                exp["operation"], actual_item.get("operation"), weight=2)

        if "storage_area" in exp:
            chk(f"item[{i}].storage_area",
                actual_item.get("storage_area") == exp["storage_area"],
                exp["storage_area"], actual_item.get("storage_area"), weight=2)

    # 4. Confirmed field
    if tc.expected_confirmed is not None:
        chk("confirmed", confirmed == tc.expected_confirmed,
            tc.expected_confirmed, confirmed, weight=2)

    # 5. Storage locked to workspace
    if tc.check_storage_locked and action in ("confirm", "update") and items:
        for i, it in enumerate(items):
            chk(f"item[{i}].storage_locked",
                it.get("storage_area") == tc.workspace_area,
                tc.workspace_area, it.get("storage_area"), weight=2)

    # 6. Expected flags present
    for flag in tc.flags_must_include:
        chk(f"flag_present:{flag}", flag in flags, True, flag in flags, weight=1)

    # 7. Forbidden flags absent
    for flag in tc.flags_must_not_include:
        chk(f"flag_absent:{flag}", flag not in flags, False, flag in flags, weight=1)

    # 8. No filler
    if tc.check_no_filler:
        msg_lower = message.lower()
        filler = next((p for p in FILLER_PHRASES if p in msg_lower), None)
        chk("no_filler", filler is None, "no filler",
            f"FILLER: '{filler}'" if filler else "clean", weight=2)

    # 9. Response brevity
    n_sent = _count_sentences(message)
    chk("max_sentences", n_sent <= tc.max_sentences,
        f"≤{tc.max_sentences}", n_sent, weight=1)

    # 10. Message must contain
    for phrase in tc.message_must_contain:
        chk(f"msg_contains:{phrase[:15]}",
            phrase in message, phrase, message[:80], weight=1)

    # 11. Message must not contain
    for phrase in tc.message_must_not_contain:
        chk(f"msg_not:{phrase[:15]}",
            phrase.lower() not in message.lower(),
            f"NOT {phrase}", message[:80], weight=1)

    # 12. Unit price positive
    if tc.check_unit_price_positive and items:
        for i, it in enumerate(items):
            price = it.get("unit_price")
            chk(f"item[{i}].price>0", price is not None and price > 0,
                ">0", price, weight=1)

    total_w  = sum(c.weight for c in checks)
    passed_w = sum(c.weight for c in checks if c.passed)
    score    = passed_w / total_w if total_w > 0 else 1.0
    # Passed = all high-weight (≥2) checks are green
    all_critical = all(c.passed for c in checks if c.weight >= 2)

    return GradeResult(
        test_id=tc.id,
        description=tc.description,
        rule=tc.rule,
        passed=all_critical,
        score=score,
        checks=checks,
        aria_result=result,
        elapsed_ms=0.0,
    )


# ── Runner ────────────────────────────────────────────────────────────────────

_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 2.0  # seconds; doubles each retry


async def run_test(tc: TestCase) -> GradeResult:
    workspace  = _workspace_ctx(tc.workspace_location, tc.workspace_area)
    inventory  = _inventory_ctx(tc.inventory_items, tc.workspace_location, tc.workspace_area, tc.inventory_total)
    conv_hist  = _conv_json(tc.conversation_history_turns)
    pending    = _pending_ctx(tc.pending_items, tc.is_affirmation, tc.affirmation_text)
    extraction = _extraction_ctx(tc.extraction_items)

    t0, error, result = time.perf_counter(), None, {}
    for attempt in range(_MAX_RETRIES):
        try:
            result = await aria_process(
                text=tc.text,
                inventory_context=inventory,
                item_history_context=tc.item_history or "No item unit history yet.",
                conversation_history_json=conv_hist,
                workspace_context=workspace,
                pending_action_context=pending,
                worker_id="eval_worker",
                today=TODAY,
                user_profile_context="No profile yet — new worker.",
                fuzzy_units_hint=tc.fuzzy_units_hint,
                pre_classified_intent=tc.pre_classified_intent,
                intent_slots=tc.intent_slots,
                extraction_context=extraction,
            )
            error = None
            break
        except Exception as exc:
            error = str(exc)
            is_rate_limit = "429" in str(exc) or "rate_limit" in str(exc).lower()
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                print(f"\n    ↻  {tc.id}: rate-limited, retry {attempt+1}/{_MAX_RETRIES-1} in {delay:.0f}s", flush=True)
                await asyncio.sleep(delay)
            else:
                result = {
                    "action": "ERROR", "intent": "ERROR",
                    "message": str(exc),
                    "data": {"items": [], "confirmed": False, "flags": []},
                }
                break

    elapsed      = (time.perf_counter() - t0) * 1000
    grade_result = grade(tc, result)
    grade_result.elapsed_ms = elapsed
    grade_result.error       = error
    return grade_result


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="ARIA prompt eval & grader")
    parser.add_argument("--tags",        nargs="*", help="Run only tests with these tags")
    parser.add_argument("--ids",         nargs="*", help="Run only these test IDs")
    parser.add_argument("--output",      default="eval/results/latest.json",
                        help="Save JSON results (default: eval/results/latest.json)")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel workers (default 2)")
    args = parser.parse_args()

    tests = list(TEST_CASES)
    if args.tags:
        tests = [t for t in tests if any(tag in t.tags for tag in args.tags)]
    if args.ids:
        tests = [t for t in tests if t.id in args.ids]

    print(f"\n{'═'*70}")
    print(f"  ARIA Prompt Eval  ·  {len(tests)} test cases  ·  concurrency={args.concurrency}")
    print(f"{'═'*70}\n")

    sem = asyncio.Semaphore(args.concurrency)

    async def run_with_sem(tc: TestCase) -> GradeResult:
        async with sem:
            print(f"  ▶  {tc.id}", flush=True)
            r = await run_test(tc)
            icon = "✅" if r.passed else "❌"
            score_str = f"{r.score*100:5.1f}%"
            print(f"  {icon}  {tc.id:<50}  {score_str}  {r.elapsed_ms:.0f}ms")
            return r

    results: list[GradeResult] = await asyncio.gather(*[run_with_sem(tc) for tc in tests])

    # ── Summary ───────────────────────────────────────────────────────────────
    passed   = sum(1 for r in results if r.passed)
    total    = len(results)
    avg_sc   = sum(r.score for r in results) / total if total else 0
    total_ms = sum(r.elapsed_ms for r in results)

    print(f"\n{'═'*70}")
    print(f"  RESULTS  {passed}/{total} passed  ·  avg {avg_sc*100:.1f}%  ·  {total_ms/1000:.1f}s total")
    print(f"{'═'*70}")

    # ── Failures detail ───────────────────────────────────────────────────────
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n{'─'*70}")
        print("  FAILURES\n")
        for r in failed:
            print(f"  ❌  {r.test_id}  [{r.rule}]")
            print(f"      {r.description}")
            if r.error:
                print(f"      ERROR: {r.error}")
            msg = r.aria_result.get("message", "")
            print(f"      ARIA: \"{msg[:120]}\"")
            act  = r.aria_result.get("action", "?")
            intent = r.aria_result.get("intent", "?")
            conf   = (r.aria_result.get("data") or {}).get("confirmed", "?")
            flags  = (r.aria_result.get("data") or {}).get("flags", [])
            items  = (r.aria_result.get("data") or {}).get("items", [])
            print(f"      action={act}  intent={intent}  confirmed={conf}  flags={flags}")
            if items:
                for it in items[:2]:
                    print(f"        item: {it.get('item_name')} {it.get('quantity')} "
                          f"{it.get('unit')} → op={it.get('operation')} "
                          f"area={it.get('storage_area')} price={it.get('unit_price')}")
            for c in r.checks:
                if not c.passed:
                    tag = f"[w={c.weight}]"
                    print(f"        {tag} {c.name}: expected={c.expected!r}  actual={c.actual!r}")
            print()

    # ── By-rule breakdown ─────────────────────────────────────────────────────
    by_rule: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0, "score_sum": 0.0})
    for r in results:
        by_rule[r.rule]["total"]     += 1
        by_rule[r.rule]["score_sum"] += r.score
        if r.passed:
            by_rule[r.rule]["passed"] += 1

    print(f"\n{'─'*70}")
    print("  BY RULE\n")
    for rule, counts in sorted(by_rule.items()):
        p, t = counts["passed"], counts["total"]
        avg  = counts["score_sum"] / t * 100
        bar  = "█" * p + "░" * (t - p)
        flag = "  ← needs work" if p < t else ""
        print(f"  {bar}  {p}/{t}  {avg:5.1f}%  {rule}{flag}")

    # ── Check-level rollup ────────────────────────────────────────────────────
    check_totals: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
    for r in results:
        for c in r.checks:
            category = c.name.split("[")[0].split(":")[0]  # strip index/value
            check_totals[category]["total"] += 1
            if c.passed:
                check_totals[category]["passed"] += 1

    print(f"\n{'─'*70}")
    print("  CHECK-LEVEL ROLLUP\n")
    for cat, counts in sorted(check_totals.items()):
        p, t = counts["passed"], counts["total"]
        pct  = p / t * 100 if t else 0
        bar  = "█" * p + "░" * (t - p)
        flag = "  ← failing" if p < t else ""
        print(f"  {bar:<28}  {p}/{t:>2}  {pct:5.1f}%  {cat}{flag}")

    # ── Prompt suggestions ────────────────────────────────────────────────────
    if failed:
        failing_rules = {r.rule for r in failed}
        print(f"\n{'─'*70}")
        print("  PROMPT IMPROVEMENT SUGGESTIONS\n")
        suggestions = {
            "Rule 0a":                    "Strengthen the denial-vs-correction examples with more 'no I meant X' cases.",
            "Rule 0b CASE A":             "Rule 0b CASE A: ensure ARIA checks last user turn for qty+unit before asking.",
            "Rule 0b CASE B":             "Rule 0b CASE B: ARIA should inherit item from prior mutative turn when qty-only arrives.",
            "Rule 0b CASE B EXCEPTION":   "Rule 0b EXCEPTION: add explicit examples of cancelled turns and query-answer turns.",
            "Rule 1":                     "Rule 1: strengthen the incompatible-unit examples for both solid/liquid directions.",
            "Rule 3":                     "Rule 3: add a concrete example showing storage_area override is ignored.",
            "Rule 4":                     "Rule 4: ensure ARIA always embeds ⚠ in message when storage is unusual.",
            "Rule 7A":                    "Rule 7A: clarify that 'total value' with no location keyword → workspace total.",
            "Rule 7B":                    "Rule 7B: clarify 'grand total' / 'all locations' → all-locations figure.",
            "Rule 9":                     "Rule 9: add examples showing item names must not be paraphrased.",
            "Rule 10":                    "Rule 10: add a note that unit_price must always be > 0.",
            "General Rule #2 (no filler)":"Remove all closing filler. Trim the GENERAL RULES #2 wording if needed.",
            "General Rule #2 (brevity)":  "Consider adding '1 sentence for confirms, 2 max' to tighten responses.",
            "General Rule #3":            "Rule 3 (affirmation): ensure the ⚡ signal always triggers action=update.",
            "General Rule #5":            "Multi-item: ensure all items are captured in the items array.",
            "Schema":                     "Check that expiry_date is passed through when present in worker message.",
        }
        for rule in sorted(failing_rules):
            if rule in suggestions:
                print(f"  • {rule}: {suggestions[rule]}")
            else:
                print(f"  • {rule}: review the failing checks above and tighten the relevant rule.")

    # ── JSON output ───────────────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": {
                "passed": passed, "total": total,
                "avg_score": round(avg_sc, 4),
                "total_elapsed_ms": round(total_ms, 1),
            },
            "by_rule": {
                rule: {"passed": v["passed"], "total": v["total"]}
                for rule, v in by_rule.items()
            },
            "results": [
                {
                    "id":          r.test_id,
                    "description": r.description,
                    "rule":        r.rule,
                    "passed":      r.passed,
                    "score":       round(r.score, 4),
                    "elapsed_ms":  round(r.elapsed_ms, 1),
                    "aria_message": r.aria_result.get("message", ""),
                    "aria_action":  r.aria_result.get("action", ""),
                    "aria_intent":  r.aria_result.get("intent", ""),
                    "aria_items": (r.aria_result.get("data") or {}).get("items", []),
                    "aria_flags": (r.aria_result.get("data") or {}).get("flags", []),
                    "aria_confirmed": (r.aria_result.get("data") or {}).get("confirmed", False),
                    "checks": [
                        {"name": c.name, "passed": c.passed, "expected": c.expected,
                         "actual": c.actual, "weight": c.weight}
                        for c in r.checks
                    ],
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(payload, indent=2))
        print(f"\n  Results saved → {out_path}")

    print()
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
