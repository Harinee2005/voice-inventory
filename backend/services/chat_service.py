"""
ChatService — SSE-streaming and synchronous entry points for the ARIA workflow.

Every turn gets a trace_id so you can grep the full end-to-end path:

    grep "a3f9c1b2" aria_output.log

At the end of every turn a TURN SUMMARY block is emitted:

    ══ TURN SUMMARY ══════════════════════════════════
    trace        : a3f9c1b2
    worker       : worker_1
    session      : sess_abc
    text         : "add 5 kg tomato"
    intent       : add  (confidence=0.98)
    guard        : passed
    extraction   : 1 items  confidence=HIGH
    action       : confirm → update (after affirmation)
    inventory    : UPDATED  tomato 0.0 kg → 5.0 kg  @ Cold Storage
    emotion      : neutral
    flags        : []
    elapsed      : 2341 ms
    ══════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import Request
from logging_config import J, new_trace, get_trace, separator

logger = logging.getLogger(__name__)

_STATUS_BY_NODE: dict[str, str] = {
    "load_context":   "Loading inventory context…",
    "load_memory":    "Retrieving worker memories…",
    "preprocess":     "Preprocessing message…",
    "intent":         "Classifying intent…",
    "extraction":     "Extracting inventory items…",
    "guard":          "Validating inventory items…",
    "aria":           "ARIA is thinking…",
    "validate":       "Validating response…",
    "execute":        "Updating inventory…",
    "persist_memory": "Saving to memory…",
    "rejected":       "Clarification needed…",
    "clarify":        "Preparing clarification…",
}

_TERMINAL_NODES = {"execute", "rejected", "clarify"}
_PING_INTERVAL = 15.0


def _sse_event(obj: dict[str, Any], *, event_name: str | None = None) -> str:
    lines: list[str] = []
    if event_name:
        lines.append(f"event: {event_name}")
    lines.append("data: " + json.dumps(obj, ensure_ascii=False, default=str))
    return "\n".join(lines) + "\n\n"


def _response_payload(delta: dict[str, Any]) -> dict[str, Any]:
    return {
        "message":           delta.get("message", ""),
        "action":            delta.get("action", "none"),
        "intent":            delta.get("intent", "unknown"),
        "data":              delta.get("data", {}),
        "inventory_updated": delta.get("inventory_updated", False),
    }


def _emit_turn_summary(
    *,
    tid: str,
    worker_id: str,
    session_id: str,
    text: str,
    final_state: dict[str, Any],
    elapsed_ms: float,
) -> None:
    """
    Emit a structured summary block at the end of every turn.
    This is the single block you read to understand what happened in a turn
    and to spot edge cases without reading every individual log line.
    """
    intent_result   = final_state.get("intent_result") or {}
    guard_result    = final_state.get("guard_result") or {}
    extraction      = final_state.get("extraction_result") or {}
    aria_result     = final_state.get("aria_result") or {}
    data            = final_state.get("data") or {}

    intent_label    = intent_result.get("intent", final_state.get("intent", "?"))
    confidence      = intent_result.get("confidence", 0.0)
    guard_status    = "SKIPPED" if not guard_result else ("REJECTED" if final_state.get("guard_rejected") else "passed")
    ext_items       = len(extraction.get("items", []))
    ext_conf        = (extraction.get("inventory_session") or {}).get("overall_confidence", "N/A")
    action          = final_state.get("action", "none")
    inv_updated     = final_state.get("inventory_updated", False)
    flags           = data.get("flags") or final_state.get("extra_flags") or []
    emotion         = (aria_result.get("user_emotion") or "neutral")
    is_affirmation  = final_state.get("is_affirmation", False)
    needs_clarify   = final_state.get("needs_clarification", False)

    # Inventory changes summary
    items = data.get("items") or []
    inv_lines = []
    for it in items:
        inv_lines.append(
            f"  item={it.get('item_name')!r}  qty={it.get('quantity')}  "
            f"unit={it.get('unit')!r}  op={it.get('operation')!r}  area={it.get('storage_area')!r}"
        )

    # Edge-case signals
    edge_cases = []
    if needs_clarify:
        edge_cases.append("[EDGE CASE] intent confidence too low → short-circuited to clarify")
    if final_state.get("guard_rejected"):
        edge_cases.append("[EDGE CASE] guard rejected message")
    if is_affirmation:
        edge_cases.append("[EDGE CASE] affirmation detected → short-circuit execution")
    if "unit_mismatch"        in flags: edge_cases.append("[EDGE CASE] unit mismatch with stored history")
    if "suspicious_quantity"  in flags: edge_cases.append("[EDGE CASE] suspicious quantity (>10× previous)")
    if "storage_warning"      in flags: edge_cases.append("[EDGE CASE] item unusual for this storage area")
    if "conflict"             in flags: edge_cases.append("[EDGE CASE] same-day conflict with another worker")
    if "not_relevant"         in flags: edge_cases.append("[EDGE CASE] non-food item rejected")
    if action == "none" and not needs_clarify and not final_state.get("guard_rejected"):
        edge_cases.append("[ANOMALY] action=none — check ARIA output and validate_node")
    if intent_label == "unknown":
        edge_cases.append("[ANOMALY] intent=unknown reached aria_node (expected short-circuit)")

    lines = [
        separator("TURN SUMMARY"),
        f"  trace        : {tid}",
        f"  worker       : {worker_id}",
        f"  session      : {session_id}",
        f"  text         : {text!r}",
        f"  intent       : {intent_label}  (confidence={confidence:.2f})",
        f"  guard        : {guard_status}",
        f"  extraction   : {ext_items} items  overall_confidence={ext_conf}",
        f"  action       : {action}",
        f"  inventory    : {'UPDATED' if inv_updated else 'not updated'}",
    ]
    for il in inv_lines:
        lines.append(il)
    lines += [
        f"  emotion      : {emotion}",
        f"  flags        : {flags}",
        f"  elapsed      : {elapsed_ms:.0f} ms",
    ]
    if edge_cases:
        lines.append("  edge_cases   :")
        for ec in edge_cases:
            lines.append(f"    {ec}")
    lines.append(separator())

    for line in lines:
        logger.info(line)


class ChatService:
    def __init__(self) -> None:
        from workflow.graph import get_graph
        self.graph = get_graph()

    async def handle_stream(
        self,
        request: Request,
        text: str,
        session_id: str,
        worker_id: str,
        storage_area: str = "",
        location_name: str = "",
    ) -> AsyncIterator[str]:
        """Yield SSE frames: start → status* → done | error."""
        from database import SessionLocal
        from services.workflow_events import (
            register_workflow_event_queue,
            unregister_workflow_event_queue,
            workflow_event_queue,
        )

        tid = new_trace()
        request_id = tid  # reuse trace as SSE request_id
        t0 = time.perf_counter()
        db = SessionLocal()

        initial_state: dict[str, Any] = {
            "text":          text,
            "session_id":    session_id,
            "worker_id":     worker_id,
            "storage_area":  storage_area,
            "location_name": location_name,
            "db":            db,
            "extra_flags":   [],
        }

        logger.info(separator(f"TURN START {tid}"))
        logger.info(
            "CHAT STREAM ──▶  worker=%s  session=%s  area=%r  text=%r",
            worker_id, session_id, storage_area, text[:200],
        )
        yield _sse_event({"request_id": request_id}, event_name="start")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        token = workflow_event_queue.set(queue)
        register_workflow_event_queue(request_id, queue)
        final_state: dict[str, Any] = {}

        async def _run_graph(q: asyncio.Queue[dict[str, Any]]) -> None:
            try:
                async for chunk in self.graph.astream(initial_state, stream_mode="updates"):
                    await q.put({"type": "graph", "chunk": chunk})
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                await q.put({"type": "error", "error": exc})
            finally:
                await q.put({"type": "graph_done"})

        graph_task = asyncio.create_task(_run_graph(queue))

        try:
            graph_done = False
            while not graph_done:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        logger.warning(
                            "CHAT STREAM CLIENT DISCONNECTED  [EDGE CASE] graph cancelled mid-run",
                        )
                        graph_task.cancel()
                        return
                    yield ": ping\n\n"
                    continue

                event_type = event.get("type")

                if event_type == "error":
                    raise event["error"]

                if event_type == "graph_done":
                    graph_done = True
                    continue

                chunk = event.get("chunk")
                if not isinstance(chunk, dict):
                    continue

                for node_name, delta in chunk.items():
                    # Accumulate state for the turn summary
                    if isinstance(delta, dict):
                        final_state.update(delta)

                    status_msg = _STATUS_BY_NODE.get(node_name)
                    if status_msg:
                        yield _sse_event({"message": status_msg}, event_name="status")

                    if node_name in _TERMINAL_NODES and isinstance(delta, dict):
                        payload = _response_payload(delta)
                        logger.info(
                            "CHAT STREAM DONE  action=%s  intent=%s  inventory_updated=%s  message=%r",
                            payload.get("action"), payload.get("intent"),
                            payload.get("inventory_updated"), (payload.get("message") or "")[:120],
                        )
                        yield _sse_event(payload, event_name="done")

            await graph_task
            _emit_turn_summary(
                tid=tid, worker_id=worker_id, session_id=session_id,
                text=text, final_state=final_state,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        except asyncio.CancelledError:
            logger.warning("CHAT STREAM CANCELLED  [EDGE CASE] server shutdown mid-stream")
            raise

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.exception(
                "CHAT STREAM ERROR  [ANOMALY] unhandled exception  elapsed=%.0fms  "
                "error_type=%s  error=%s",
                elapsed_ms, type(exc).__name__, exc,
            )
            yield _sse_event(
                {"message": str(exc), "code": type(exc).__name__},
                event_name="error",
            )

        finally:
            unregister_workflow_event_queue(request_id)
            workflow_event_queue.reset(token)
            if not graph_task.done():
                graph_task.cancel()
            db.close()

    async def handle(
        self,
        text: str,
        session_id: str,
        worker_id: str,
        storage_area: str = "",
        location_name: str = "",
    ) -> dict[str, Any]:
        """Run the full workflow synchronously and return the final response dict."""
        from database import SessionLocal

        tid = new_trace()
        t0 = time.perf_counter()

        logger.info(separator(f"TURN START {tid}"))
        logger.info(
            "CHAT SYNC ──▶  worker=%s  session=%s  area=%r  text=%r",
            worker_id, session_id, storage_area, text[:200],
        )

        db = SessionLocal()
        try:
            initial_state: dict[str, Any] = {
                "text":          text,
                "session_id":    session_id,
                "worker_id":     worker_id,
                "storage_area":  storage_area,
                "location_name": location_name,
                "db":            db,
                "extra_flags":   [],
            }
            final_state = await self.graph.ainvoke(initial_state)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            result = {
                "message":           final_state.get("message", ""),
                "action":            final_state.get("action", "none"),
                "intent":            final_state.get("intent", "unknown"),
                "data":              final_state.get("data", {}),
                "inventory_updated": final_state.get("inventory_updated", False),
            }

            _emit_turn_summary(
                tid=tid, worker_id=worker_id, session_id=session_id,
                text=text, final_state=final_state, elapsed_ms=elapsed_ms,
            )
            return result

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error(
                "CHAT SYNC ERROR  [ANOMALY] elapsed=%.0fms  error_type=%s  error=%s",
                elapsed_ms, type(exc).__name__, exc,
            )
            raise
        finally:
            db.close()
