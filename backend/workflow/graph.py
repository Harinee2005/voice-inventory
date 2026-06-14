"""
LangGraph workflow graph for voice-inventory ARIA.

No checkpointer is used — each request is an independent stateless run.

Graph topology:

    START
      ↓ (parallel fan-out)
    load_context    load_memory
      ↓                  ↓
      └──── preprocess ───┘   (fan-in)
                  ↓
             intent_node       ← dedicated classifier (gpt-4o-mini, temp=0)
                  ↓
         ┌────────┴────────┐
      clarify            guard           (low-confidence intent → clarify)
         ↓                  ↓
        END      ┌──────────┴──────────┐
              rejected            extraction      ← strict item extraction
                 ↓                     ↓
                END                  aria
                                      ↓
                                  validate
                                      ↓
                                  execute
                                      ↓
                               persist_memory
                                      ↓
                                     END
"""

from langgraph.graph import StateGraph, START, END

from workflow.state import WorkflowState
from workflow.nodes import (
    load_context_node,
    load_memory_node,
    preprocess_node,
    intent_node,
    clarify_node,
    route_after_intent,
    guard_node,
    route_after_guard,
    rejected_node,
    extraction_node,
    priority_node,
    aria_node,
    validate_node,
    execute_node,
    persist_memory_node,
)


def build_graph():
    graph = StateGraph(WorkflowState)

    graph.add_node("load_context",   load_context_node)
    graph.add_node("load_memory",    load_memory_node)
    graph.add_node("preprocess",     preprocess_node)
    graph.add_node("intent",         intent_node)
    graph.add_node("clarify",        clarify_node)
    graph.add_node("guard",          guard_node)
    graph.add_node("rejected",       rejected_node)
    graph.add_node("extraction",     extraction_node)
    graph.add_node("priority",       priority_node)
    graph.add_node("aria",           aria_node)
    graph.add_node("validate",       validate_node)
    graph.add_node("execute",        execute_node)
    graph.add_node("persist_memory", persist_memory_node)

    # Parallel fan-out from START
    graph.add_edge(START, "load_context")
    graph.add_edge(START, "load_memory")

    # Fan-in at preprocess
    graph.add_edge("load_context", "preprocess")
    graph.add_edge("load_memory",  "preprocess")

    # Intent classification gate
    graph.add_edge("preprocess", "intent")
    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {"clarify": "clarify", "guard": "guard"},
    )
    graph.add_edge("clarify", END)

    # Guard gate
    graph.add_conditional_edges(
        "guard",
        route_after_guard,
        {"rejected": "rejected", "extraction": "extraction"},
    )
    graph.add_edge("rejected",   END)

    # Extraction → Priority (BM25 pruning) → ARIA → happy path
    graph.add_edge("extraction",      "priority")
    graph.add_edge("priority",        "aria")
    graph.add_edge("aria",            "validate")
    graph.add_edge("validate",        "execute")
    graph.add_edge("execute",         "persist_memory")
    graph.add_edge("persist_memory",  END)

    return graph.compile()   # no checkpointer — stateless per-request


# Module-level singleton — compiled once on first import
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
