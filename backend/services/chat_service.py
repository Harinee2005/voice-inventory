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

def _trunc(s: str, n: int = 100) -> str:
    return (s[:n] + "…") if len(s) > n else s


def _build_rich_status(node_name: str, delta: dict, state: dict | None = None) -> dict | None:
    """Build a rich status event from a completed node's delta + accumulated state.

    Returns a dict with step/icon/label/detail/reasoning/input/output so the
    frontend can drill into what each agent received, thought, and decided.
    state contains all keys accumulated from prior nodes (useful for ARIA/validate
    which need to know what context was available upstream).
    """
    s = state or {}

    # ── load_context ─────────────────────────────────────────────────────────
    if node_name == "load_context":
        inv = delta.get("inventory_context") or ""
        conv = delta.get("conversation_history") or []
        pending = delta.get("pending_action")
        item_count = inv.count("•")
        parts = [f"{item_count} items in stock"]
        if conv:
            parts.append(f"{len(conv)} conv turns")
        if pending:
            parts.append("pending action")
        inv_lines = [l.strip() for l in inv.split("\n") if "•" in l][:10]
        recent_turns = [
            {"role": t.get("role", "?"), "said": _trunc(t.get("content", ""), 100)}
            for t in (conv[-4:] if conv else [])
        ]
        return {
            "step": "context", "icon": "📦", "label": "Context",
            "detail": " · ".join(parts),
            "input": {
                "worker": s.get("worker_id", "?"),
                "storage_area": s.get("storage_area") or "none",
                "location": s.get("location_name") or "none",
            },
            "output": {
                "inventory_items": item_count,
                "inventory_data": inv_lines or None,
                "conversation_turns": len(conv),
                "recent_turns": recent_turns or None,
                "pending_action": bool(pending),
                "rejection_log": bool(delta.get("rejection_context")),
                "session_digest": bool(delta.get("session_digest")),
            },
        }

    # ── load_memory ───────────────────────────────────────────────────────────
    if node_name == "load_memory":
        memories = delta.get("user_memories") or []
        detail = f"{len(memories)} worker memories" if memories else "no memories yet"
        memory_texts = [
            _trunc((m.get("memory") or m.get("text") or "").strip(), 120)
            for m in memories[:5]
            if (m.get("memory") or m.get("text") or "").strip()
        ]
        return {
            "step": "memory", "icon": "🧬", "label": "Memory",
            "detail": detail,
            "input": {"worker": s.get("worker_id", "?"), "source": "Mem0"},
            "output": {
                "memories_loaded": len(memories),
                "profile_source": "Mem0" if memories else "DB (fallback)",
                "memories": memory_texts or None,
            },
        }

    # ── preprocess ────────────────────────────────────────────────────────────
    if node_name == "preprocess":
        is_aff = delta.get("is_affirmation", False)
        fuzzy = delta.get("fuzzy_units_hint") or ""
        fragment = delta.get("fragment_hint") or ""
        text = s.get("text", "")
        detail = ("affirmation → fast path" if is_aff
                  else "fuzzy unit detected" if fuzzy
                  else "clean")
        return {
            "step": "preprocess", "icon": "⚙️", "label": "Preprocess",
            "detail": detail,
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

    # ── intent ────────────────────────────────────────────────────────────────
    if node_name == "intent":
        ir = delta.get("intent_result") or {}
        intent = ir.get("intent", "?")
        conf = ir.get("confidence", 0.0)
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
            "step": "intent", "icon": "🧠", "label": "Intent",
            "detail": detail,
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

    # ── screen_extract (merged guard + extraction, one LLM call) ─────────────
    if node_name == "screen_extract":
        er = delta.get("extraction_result") or {}
        gr = delta.get("guard_result") or {}
        rejected = delta.get("guard_rejected", False)
        text = s.get("text", "")
        if not er and not gr:
            return {
                "step": "screen_extract", "icon": "🛡️", "label": "Screen+Extract",
                "detail": "skipped",
                "input": {"reason": "affirmation / query / short message"},
                "output": {"items": None},
            }
        items = er.get("items") or []
        conf = (er.get("inventory_session") or {}).get("overall_confidence", "")
        if rejected:
            flagged = [i.get("name", "?") for i in gr.get("items", []) if not i.get("is_valid")]
            detail = "rejected — " + (", ".join(flagged[:3]) or "non-food item")
        elif items:
            names = ", ".join(
                f"{i.get('canonical_name') or i.get('raw_text', '?')} "
                f"{i.get('quantity', '') or ''} {i.get('unit', '') or ''}".strip()
                for i in items[:2]
            )
            detail = f"{len(items)} item{'s' if len(items) != 1 else ''} · {names}"
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
                "food": i.get("is_food", True),
                "ambiguous": i.get("is_ambiguous", False),
                "concern": i.get("concern") or None,
                "errors": ", ".join(i.get("validation_errors") or []) or None,
            }
            for i in items
        ]
        from clients.llm_client import SCREEN_MODEL
        return {
            "step": "screen_extract", "icon": "🛡️", "label": "Screen+Extract",
            "detail": detail,
            "input": {
                "message": _trunc(text, 80),
                "model": SCREEN_MODEL,
            },
            "output": {
                "result": "rejected" if rejected else "passed",
                "overall_confidence": conf or "?",
                "items": items_out or None,
                "guard_message": gr.get("guard_message") or None,
            },
        }

    # ── priority ──────────────────────────────────────────────────────────────
    if node_name == "priority":
        focus = delta.get("priority_focus") or ""
        ph = delta.get("priority_conversation_history") or []
        filtered_inv = delta.get("priority_inventory_context") or ""
        orig_inv = s.get("inventory_context") or ""
        orig_hist = len(s.get("conversation_history") or [])
        detail = f"focus: {focus}" if focus else f"{len(ph)} turns kept"
        return {
            "step": "priority", "icon": "📌", "label": "Priority",
            "detail": detail,
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

    # ── aria ──────────────────────────────────────────────────────────────────
    if node_name == "aria":
        ar = delta.get("aria_result") or {}
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

        # Build what ARIA saw (from accumulated priority context)
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
            {
                "raw": i.get("raw_text", "?"),
                "canonical": i.get("canonical_name"),
                "qty": i.get("quantity"),
                "unit": i.get("unit", ""),
                "confidence": i.get("confidence", "?"),
            }
            for i in ext_items[:4]
        ]
        items_out = [
            {
                "name": i.get("item_name"),
                "quantity": i.get("quantity"),
                "unit": i.get("unit", ""),
                "operation": i.get("operation", ""),
                "storage_area": i.get("storage_area", ""),
                "category": i.get("category", ""),
            }
            for i in items
        ]

        return {
            "step": "aria", "icon": "✨", "label": "ARIA",
            "detail": " · ".join(parts),
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
                "new_lexicons": [l.get("original_word", "") for l in new_lexicons if l.get("original_word")] or None,
                "personality_note": _trunc(personality, 150) if personality else None,
            },
        }

    # ── validate ──────────────────────────────────────────────────────────────
    if node_name == "validate":
        flags = delta.get("extra_flags") or []
        ar = delta.get("aria_result") or {}
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
            "step": "validate", "icon": "✔️", "label": "Validate",
            "detail": detail,
            "input": {
                "action": action,
                "items": [
                    {"name": i.get("item_name"), "qty": i.get("quantity"),
                     "unit": i.get("unit", ""), "category": i.get("category", "")}
                    for i in items[:4]
                ] or None,
            },
            "output": {
                "action_after": action,
                "flags": [{"flag": f, "meaning": _flag_info.get(f, f)} for f in flags] or None,
                "clean": not bool(flags),
            },
        }

    # ── execute ───────────────────────────────────────────────────────────────
    if node_name == "execute":
        updated = delta.get("inventory_updated", False)
        action = delta.get("action", "?")
        items = (delta.get("data") or {}).get("items") or []
        flags = (delta.get("data") or {}).get("flags") or []
        if updated and items:
            names = ", ".join(
                f"{i.get('item_name', '?')} {i.get('quantity', '')} {i.get('unit', '')}".strip()
                for i in items[:2] if i.get("item_name")
            )
            detail = f"saved · {names}" if names else "inventory updated"
        elif action == "confirm":
            detail = "stored · awaiting confirmation"
        else:
            detail = "no DB write"
        items_written = [
            {"name": i.get("item_name"), "qty": i.get("quantity"),
             "unit": i.get("unit", ""), "op": i.get("operation", ""),
             "area": i.get("storage_area", "")}
            for i in items if i.get("item_name")
        ] if updated else None
        return {
            "step": "execute", "icon": "💾", "label": "Execute",
            "detail": detail,
            "input": {
                "action": action,
                "items_to_process": len(items),
            },
            "output": {
                "inventory_updated": updated,
                "pending_stored": action == "confirm" and not updated,
                "items_written": items_written,
                "flags": flags or None,
            },
        }

    # ── clarify / rejected ────────────────────────────────────────────────────
    if node_name in ("clarify", "rejected"):
        msg = delta.get("message") or ""
        icon = "🚫" if node_name == "rejected" else "❓"
        label = "Guard" if node_name == "rejected" else "Clarify"
        ir = s.get("intent_result") or {}
        return {
            "step": "clarify", "icon": icon, "label": label,
            "detail": _trunc(msg, 70),
            "input": {
                "intent": ir.get("intent", "?"),
                "confidence": f"{ir.get('confidence', 0):.0%}",
                "reason": node_name,
            },
            "output": {"message_to_worker": msg},
        }

    # persist_memory fires after done is emitted — skip to avoid post-done SSE noise
    return None

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

                # Streamed ARIA message tokens (ARIA_STREAMING=1) — the frontend
                # already renders 'chunk' events with a typing cursor and replaces
                # the streamed text atomically with done.message.
                if event_type == "message_chunk":
                    yield _sse_event({"text": event.get("text", "")}, event_name="chunk")
                    continue

                chunk = event.get("chunk")
                if not isinstance(chunk, dict):
                    continue

                for node_name, delta in chunk.items():
                    # Accumulate state for the turn summary
                    if isinstance(delta, dict):
                        final_state.update(delta)

                    status_event = _build_rich_status(node_name, delta if isinstance(delta, dict) else {}, final_state)
                    if status_event:
                        yield _sse_event(status_event, event_name="status")

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
