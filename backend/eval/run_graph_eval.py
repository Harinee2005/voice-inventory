#!/usr/bin/env python3
"""
ARIA full-graph integration eval.

Unlike eval/run_eval.py (which tests aria_process() with synthetic context),
this drives multi-turn scenarios through ChatService.handle() — the real
production path: LangGraph, intent/guard/extraction agents, validate_node,
execute_node, and actual DB reads/writes — against a throwaway SQLite DB.

Every turn can assert on the response AND on database state (inventory rows,
pending-action rows), which the prompt eval can never see.

Usage (from backend/):
    python -m eval.run_graph_eval
    python -m eval.run_graph_eval --ids add_confirm_affirm qty_bleed_blocked
    python -m eval.run_graph_eval --tags happy_path
    python -m eval.run_graph_eval --skip-tags gate_b3     # skip known pre-B3 gates
    python -m eval.run_graph_eval --output eval/results/graph_latest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ── Path & env setup — MUST run before any project import ────────────────────
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

_env_path = _BACKEND / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Force an isolated SQLite DB and disable external memory backends.
_DB_FILE = _BACKEND / "eval_graph_tmp.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ.pop("MEM0_API_KEY", None)

from eval.common import CheckResult, weighted_score, all_critical_passed  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────
LOCATION = "Main Kitchen"
AREA = "Fridge"

# Seeded rows → workspace total 10*8.50 + 5*1.20 + 3*2.00 = $97.00
SEED_ITEMS = [
    {"item_name": "Chicken Breast", "category": "Meat",       "quantity": 10.0, "unit": "kg",    "unit_price": 8.50},
    {"item_name": "Milk",           "category": "Dairy",      "quantity": 5.0,  "unit": "litre", "unit_price": 1.20},
    {"item_name": "Tomatoes",       "category": "Vegetables", "quantity": 3.0,  "unit": "kg",    "unit_price": 2.00},
]
WORKSPACE_TOTAL = "97.00"


# ── Scenario schema ───────────────────────────────────────────────────────────

@dataclass
class TurnSpec:
    text: str
    # Response assertions (None = don't check). Strings or tuples = any-of.
    expected_action: Optional[tuple[str, ...]] = None
    expected_intent: Optional[tuple[str, ...]] = None
    message_contains: list[str] = field(default_factory=list)
    # DB assertions, evaluated AFTER the turn completes.
    # Each: {"item_name": str, "quantity": float?, "unit": str?}
    expect_inventory: list[dict] = field(default_factory=list)
    expect_no_item: list[str] = field(default_factory=list)
    expect_pending: Optional[bool] = None


@dataclass
class GraphScenario:
    id: str
    description: str
    tags: list[str]
    turns: list[TurnSpec]
    # Workspace storage area for every turn. Pick one where the scenario's
    # items are storage-appropriate, or validate_node's storage warning will
    # legitimately downgrade confirm→clarify and fail the action check.
    area: str = AREA


@dataclass
class TurnGrade:
    text: str
    response: dict
    checks: list[CheckResult]
    elapsed_ms: float
    error: Optional[str] = None


@dataclass
class ScenarioResult:
    id: str
    description: str
    tags: list[str]
    passed: bool
    score: float
    turns: list[TurnGrade]


def _any_of(v) -> Optional[tuple[str, ...]]:
    if v is None:
        return None
    return (v,) if isinstance(v, str) else tuple(v)


def T(text: str, action=None, intent=None, contains=None,
      inventory=None, no_item=None, pending=None) -> TurnSpec:
    return TurnSpec(
        text=text,
        expected_action=_any_of(action),
        expected_intent=_any_of(intent),
        message_contains=contains or [],
        expect_inventory=inventory or [],
        expect_no_item=no_item or [],
        expect_pending=pending,
    )


# ── Scenarios ─────────────────────────────────────────────────────────────────
# Each scenario runs in its own session — pending actions and history are per-session.
#
# ORDER MATTERS: read-only scenarios run first, before any scenario writes to
# the shared DB — the analytics scenario asserts the exact seeded total.

SCENARIOS: list[GraphScenario] = [

    # ── Read-only scenarios (must run before any write) ───────────────────────

    GraphScenario(
        id="analytics_workspace_total",
        description="Total-value query returns the exact Python-computed workspace total",
        tags=["analytics", "gate_b2", "readonly"],
        turns=[
            T("what is the total value of my inventory?",
              action="query_result", intent=("analytics", "query"),
              contains=[WORKSPACE_TOTAL]),
        ],
    ),

    GraphScenario(
        id="query_no_write",
        description="Stock query answers from context and never mutates inventory",
        tags=["query", "readonly"],
        turns=[
            T("how much milk do we have?",
              action="query_result", contains=["5"],
              inventory=[{"item_name": "Milk", "quantity": 5.0, "unit": "litre"}],
              pending=False),
        ],
    ),

    GraphScenario(
        id="low_confidence_clarify",
        description="Unintelligible input short-circuits to a clarification question",
        tags=["clarify", "readonly"],
        turns=[
            T("umm the thing over there", action="clarify", pending=False),
        ],
    ),

    GraphScenario(
        id="guard_rejection_no_db_write",
        description="Non-food item is rejected and never touches the DB",
        tags=["guard", "readonly"],
        turns=[
            T("Add 2 motorbikes to the inventory",
              action=("clarify", "none"), no_item=["motorbike"], pending=False),
        ],
    ),

    # ── Mutating scenarios ─────────────────────────────────────────────────────

    GraphScenario(
        id="add_confirm_affirm",
        description="Add novel item → confirm + pending stored, no write; 'yes' → row written, pending cleared",
        tags=["happy_path", "affirmation"],
        turns=[
            T("Add 5 kg of strawberries",
              action="confirm", intent="add", pending=True, no_item=["strawberr"]),
            T("yes",
              action="update",
              inventory=[{"item_name": "strawberries", "quantity": 5.0, "unit": "kg"}],
              pending=False),
        ],
    ),

    GraphScenario(
        id="qty_bleed_blocked",
        description="After confirming basil 5 kg, bare 'add tamarind' must NOT inherit the 5",
        tags=["quantity_grounding"],
        turns=[
            T("Add 5 kg of basil", action="confirm", pending=True),
            T("yes", action="update",
              inventory=[{"item_name": "basil", "quantity": 5.0, "unit": "kg"}]),
            T("add tamarind", action="clarify", no_item=["tamarind"]),
        ],
    ),

    GraphScenario(
        id="fragment_flow_combines",
        description="'add 5 kg' → clarify; 'wheat' → combined confirm; 'yes' → 5 kg wheat written",
        tags=["fragment"],
        area="Dry Storage",
        turns=[
            T("add 5 kg", action="clarify"),
            T("wheat", action="confirm", pending=True),
            T("yes",
              inventory=[{"item_name": "wheat", "quantity": 5.0, "unit": "kg"}],
              pending=False),
        ],
    ),

    GraphScenario(
        id="deny_clears_pending",
        description="Deny after a confirm clears the pending action and writes nothing; 'litres' must not trigger a typo question",
        tags=["denial", "fuzzy_units"],
        turns=[
            T("Add 3 litres of orange juice", action="confirm", pending=True),
            T("no, cancel that", action="none", pending=False,
              no_item=["orange juice"]),
        ],
    ),

    GraphScenario(
        id="unit_incompatible_rice_litres",
        description="'5 litres of rice' must end up stored in kg (RULE 1 → deterministic in B3)",
        tags=["unit", "gate_b3"],
        area="Dry Storage",
        turns=[
            T("Add 5 litres of rice", action=("update", "confirm")),
            # If turn 1 auto-executed, this 'yes' is a stray affirmation — DB state
            # is what matters, so only the inventory assertion is checked here.
            T("yes",
              inventory=[{"item_name": "rice", "quantity": 5.0, "unit": "kg"}]),
        ],
    ),

    GraphScenario(
        id="multi_item_add",
        description="Two items in one utterance → both captured, both written on confirm",
        tags=["multi_item"],
        area="Dry Storage",
        turns=[
            T("Add 2 kg of onions and 3 bottles of olive oil",
              action="confirm", pending=True),
            T("yes",
              inventory=[
                  {"item_name": "onions", "quantity": 2.0, "unit": "kg"},
                  {"item_name": "olive oil", "quantity": 3.0},
              ],
              pending=False),
        ],
    ),
]


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_check_inventory(db, spec: dict, checks: list[CheckResult]) -> None:
    from sqlalchemy import func
    from models import InventoryItem

    name = spec["item_name"]
    row = (
        db.query(InventoryItem)
        .filter(func.lower(InventoryItem.item_name) == name.lower())
        .order_by(InventoryItem.timestamp.desc())
        .first()
    )
    label = f"db:{name}"
    if row is None:
        checks.append(CheckResult(label + ".exists", False, "row present", "missing", 3))
        return
    checks.append(CheckResult(label + ".exists", True, "row present", "present", 3))
    if "quantity" in spec:
        ok = math.isclose(row.quantity, spec["quantity"], rel_tol=1e-6)
        checks.append(CheckResult(label + ".qty", ok, str(spec["quantity"]), str(row.quantity), 3))
    if "unit" in spec:
        expected_u = spec["unit"].lower().rstrip("s")
        actual_u = (row.unit or "").lower().rstrip("s")
        checks.append(CheckResult(label + ".unit", actual_u == expected_u, spec["unit"], row.unit or "", 2))


def _db_check_no_item(db, name: str, checks: list[CheckResult]) -> None:
    from sqlalchemy import func
    from models import InventoryItem

    count = (
        db.query(InventoryItem)
        .filter(func.lower(InventoryItem.item_name).contains(name.lower()))
        .count()
    )
    checks.append(CheckResult(f"db:no_{name}", count == 0, "no rows", f"{count} rows", 3))


def _db_check_pending(db, session_id: str, expected: bool, checks: list[CheckResult]) -> None:
    from models import PendingAction

    row = db.query(PendingAction).filter(PendingAction.session_id == session_id).first()
    actual = row is not None
    checks.append(CheckResult(
        "db:pending", actual == expected,
        "pending stored" if expected else "no pending",
        "stored" if actual else "absent", 2,
    ))


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_scenario(svc, sc: GraphScenario) -> ScenarioResult:
    from database import SessionLocal

    session_id = f"graph_eval_{sc.id}"
    worker_id = "eval_worker"
    turn_grades: list[TurnGrade] = []

    for turn in sc.turns:
        checks: list[CheckResult] = []
        response: dict = {}
        error: Optional[str] = None
        t0 = time.perf_counter()
        try:
            response = await svc.handle(
                text=turn.text,
                session_id=session_id,
                worker_id=worker_id,
                storage_area=sc.area,
                location_name=LOCATION,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if error:
            checks.append(CheckResult("turn_completed", False, "response", error[:150], 3))
        else:
            if turn.expected_action is not None:
                actual = response.get("action", "")
                checks.append(CheckResult(
                    "action", actual in turn.expected_action,
                    "|".join(turn.expected_action), actual, 3))
            if turn.expected_intent is not None:
                actual = response.get("intent", "")
                checks.append(CheckResult(
                    "intent", actual in turn.expected_intent,
                    "|".join(turn.expected_intent), actual, 2))
            msg = response.get("message", "") or ""
            for needle in turn.message_contains:
                checks.append(CheckResult(
                    f"msg_contains:{needle}", needle.lower() in msg.lower(),
                    needle, msg[:120], 2))

        db = SessionLocal()
        try:
            for spec in turn.expect_inventory:
                _db_check_inventory(db, spec, checks)
            for name in turn.expect_no_item:
                _db_check_no_item(db, name, checks)
            if turn.expect_pending is not None:
                _db_check_pending(db, session_id, turn.expect_pending, checks)
        finally:
            db.close()

        turn_grades.append(TurnGrade(
            text=turn.text, response=response, checks=checks,
            elapsed_ms=elapsed_ms, error=error,
        ))

    all_checks = [c for tg in turn_grades for c in tg.checks]
    return ScenarioResult(
        id=sc.id, description=sc.description, tags=sc.tags,
        passed=all_critical_passed(all_checks),
        score=weighted_score(all_checks),
        turns=turn_grades,
    )


def _print_report(results: list[ScenarioResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    print()
    print("─" * 70)
    print(f"  GRAPH EVAL  ·  {passed}/{len(results)} scenarios passed")
    print("─" * 70)
    for r in results:
        icon = "✅" if r.passed else "❌"
        total_ms = sum(t.elapsed_ms for t in r.turns)
        print(f"  {icon}  {r.id:<36} score={r.score:.2f}  turns={len(r.turns)}  {total_ms:.0f}ms")
        if not r.passed:
            for tg in r.turns:
                for c in tg.checks:
                    if not c.passed:
                        print(f"        ✗ [{tg.text[:40]!r}] {c.name}: expected {c.expected!r}, got {c.actual!r}")
    print("─" * 70)


def _setup_db() -> None:
    if _DB_FILE.exists():
        _DB_FILE.unlink()
    from database import engine, Base, create_views
    import models  # noqa: F401 — registers tables on Base
    Base.metadata.create_all(engine)
    create_views()

    from database import SessionLocal
    from models import InventoryItem
    db = SessionLocal()
    try:
        for spec in SEED_ITEMS:
            db.add(InventoryItem(
                **spec, storage_area=AREA, location_name=LOCATION,
                updated_by="seed",
            ))
        db.commit()
    finally:
        db.close()


def _teardown_db() -> None:
    from database import engine
    engine.dispose()
    if _DB_FILE.exists():
        _DB_FILE.unlink()


async def main() -> int:
    parser = argparse.ArgumentParser(description="ARIA full-graph integration eval")
    parser.add_argument("--ids", nargs="*", help="Run only these scenario ids")
    parser.add_argument("--tags", nargs="*", help="Run only scenarios with any of these tags")
    parser.add_argument("--skip-tags", nargs="*", help="Skip scenarios with any of these tags")
    parser.add_argument("--output", help="Save results JSON to this path")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.ids:
        scenarios = [s for s in scenarios if s.id in args.ids]
    if args.tags:
        scenarios = [s for s in scenarios if set(s.tags) & set(args.tags)]
    if args.skip_tags:
        scenarios = [s for s in scenarios if not set(s.tags) & set(args.skip_tags)]
    if not scenarios:
        print("No scenarios matched the filters.")
        return 2

    _setup_db()
    try:
        from services.chat_service import ChatService
        svc = ChatService()

        print(f"  ARIA Graph Eval  ·  {len(scenarios)} scenarios  ·  sqlite={_DB_FILE.name}")
        results: list[ScenarioResult] = []
        # Sequential on purpose: turns within a scenario are stateful, and
        # serial execution keeps us clear of Claude API rate limits / the breaker.
        for sc in scenarios:
            print(f"  ▶ {sc.id} ...", flush=True)
            results.append(await run_scenario(svc, sc))

        _print_report(results)

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "summary": {
                    "passed": sum(1 for r in results if r.passed),
                    "total": len(results),
                    "avg_score": sum(r.score for r in results) / len(results),
                },
                "results": [asdict(r) for r in results],
            }, indent=2, default=str))
            print(f"  Results saved → {args.output}")

        return 0 if all(r.passed for r in results) else 1
    finally:
        _teardown_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
