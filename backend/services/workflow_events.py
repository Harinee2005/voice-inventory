"""In-process workflow progress events for SSE streaming.

LangGraph only emits outer workflow updates when a node finishes. Nodes can
publish progress here while running so the UI status bar updates mid-node.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

workflow_event_queue: ContextVar[asyncio.Queue[dict[str, Any]] | None] = ContextVar(
    "workflow_event_queue",
    default=None,
)
_queues_by_request_id: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def register_workflow_event_queue(
    request_id: str,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    if request_id:
        _queues_by_request_id[request_id] = queue
        logger.debug("WORKFLOW EVENT QUEUE REGISTERED  request_id=%s  active_queues=%d", request_id, len(_queues_by_request_id))


def unregister_workflow_event_queue(request_id: str) -> None:
    if request_id:
        _queues_by_request_id.pop(request_id, None)
        logger.debug("WORKFLOW EVENT QUEUE UNREGISTERED  request_id=%s  active_queues=%d", request_id, len(_queues_by_request_id))


async def emit_workflow_progress(
    step: dict[str, Any],
    *,
    request_id: str | None = None,
) -> None:
    queue = workflow_event_queue.get()
    if queue is None and request_id:
        queue = _queues_by_request_id.get(request_id)
    if queue is None:
        return
    logger.debug("WORKFLOW PROGRESS EMIT  request_id=%s  step=%s", request_id, step)
    await queue.put({"type": "progress", "step": step})


async def emit_message_chunk(text: str) -> None:
    """Push a chunk of ARIA's streamed message text to the SSE loop.

    No-op when there is no registered queue (sync /api/chat path, eval runs) —
    streaming is purely additive to the SSE contract.
    """
    queue = workflow_event_queue.get()
    if queue is None or not text:
        return
    await queue.put({"type": "message_chunk", "text": text})
