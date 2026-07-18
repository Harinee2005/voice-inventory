"""
Shared grading primitives for the ARIA eval suites.

Used by:
  - eval/run_eval.py        (prompt-level eval of aria_process in isolation)
  - eval/run_graph_eval.py  (full-graph integration eval through ChatService)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    passed: bool
    expected: str
    actual: str
    weight: int


def weighted_score(checks: list[CheckResult]) -> float:
    """0.0–1.0 weighted pass ratio."""
    total = sum(c.weight for c in checks)
    if total == 0:
        return 1.0
    return sum(c.weight for c in checks if c.passed) / total


def all_critical_passed(checks: list[CheckResult]) -> bool:
    """A run passes iff every check with weight >= 2 passed."""
    return all(c.passed for c in checks if c.weight >= 2)
