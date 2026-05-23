"""
Chat router — SSE streaming and sync chat endpoints plus Mem0 memory CRUD.

Endpoints:
  POST /api/chat          — sync turn (returns JSON)
  POST /api/chat/stream   — SSE stream (text/event-stream)
  GET  /api/memory        — list worker memories
  POST /api/memory        — add a memory
  DELETE /api/memory/item/{id} — delete one memory item
  DELETE /api/memory/entity    — clear all memories for a worker
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.chat_service import ChatService

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)

_CHAT_SERVICE: ChatService | None = None


def get_chat_service() -> ChatService:
    global _CHAT_SERVICE
    if _CHAT_SERVICE is None:
        _CHAT_SERVICE = ChatService()
    return _CHAT_SERVICE


class ChatRequest(BaseModel):
    text: str
    session_id: str
    worker_id: str = "worker"
    storage_area: Optional[str] = Field(default="")
    location_name: Optional[str] = Field(default="")


class MemoryAddRequest(BaseModel):
    worker_id: str
    content: str
    metadata: Optional[dict] = None


@router.post("/chat")
async def chat(
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> Any:
    logger.info(
        "CHAT /chat  worker=%s  session=%s  text=%r  area=%r",
        body.worker_id, body.session_id, body.text[:120], body.storage_area,
    )
    result = await service.handle(
        text=body.text,
        session_id=body.session_id,
        worker_id=body.worker_id,
        storage_area=body.storage_area or "",
        location_name=body.location_name or "",
    )
    logger.info(
        "CHAT /chat DONE  action=%s  intent=%s  inventory_updated=%s",
        result.get("action"), result.get("intent"), result.get("inventory_updated"),
    )
    return result


@router.post("/chat/stream")
async def chat_stream(
    req: Request,
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    logger.info(
        "CHAT /chat/stream  worker=%s  session=%s  text=%r  area=%r",
        body.worker_id, body.session_id, body.text[:120], body.storage_area,
    )
    return StreamingResponse(
        service.handle_stream(
            request=req,
            text=body.text,
            session_id=body.session_id,
            worker_id=body.worker_id,
            storage_area=body.storage_area or "",
            location_name=body.location_name or "",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/memory")
async def list_memory(
    worker_id: str = Query(..., description="Worker ID to list memories for."),
) -> dict[str, Any]:
    logger.info("MEMORY GET  worker=%s", worker_id)
    from clients.mem0_client import get_user_memories
    memories = await get_user_memories(worker_id)
    logger.info("MEMORY GET DONE  worker=%s  count=%d", worker_id, len(memories))
    return {"worker_id": worker_id, "count": len(memories), "items": memories}


@router.post("/memory")
async def add_memory(body: MemoryAddRequest) -> dict[str, Any]:
    logger.info("MEMORY ADD  worker=%s  content=%r", body.worker_id, body.content[:120])
    from clients.mem0_client import add_user_memory
    result = await add_user_memory(
        body.content, body.worker_id, metadata=body.metadata
    )
    logger.info("MEMORY ADD DONE  worker=%s  result=%s", body.worker_id, bool(result))
    return {"worker_id": body.worker_id, "result": result}


@router.delete("/memory/item/{memory_id}")
async def delete_memory_item(memory_id: str) -> dict[str, Any]:
    logger.info("MEMORY DELETE item  memory_id=%s", memory_id)
    import asyncio
    from clients.mem0_client import _get_client

    if not memory_id.strip():
        raise HTTPException(status_code=400, detail="memory_id is required")

    try:
        client = _get_client()
        await asyncio.to_thread(client.delete, memory_id.strip())
        logger.info("MEMORY DELETED  memory_id=%s", memory_id.strip())
        return {"deleted": True, "memory_id": memory_id.strip()}
    except Exception as exc:
        logger.error("MEMORY DELETE ERROR  memory_id=%s  error=%s", memory_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/memory/entity")
async def clear_memory_for_worker(
    worker_id: str = Query(..., description="Worker ID whose memories to clear."),
) -> dict[str, Any]:
    logger.warning("MEMORY CLEAR ALL  worker=%s  (all memories will be deleted)", worker_id)
    import asyncio
    from clients.mem0_client import _get_client

    if not worker_id.strip():
        raise HTTPException(status_code=400, detail="worker_id is required")

    try:
        client = _get_client()
        await asyncio.to_thread(
            client.delete_all, filters={"user_id": worker_id.strip()}
        )
        logger.info("MEMORY CLEARED  worker=%s", worker_id.strip())
        return {"deleted": True, "worker_id": worker_id.strip()}
    except Exception as exc:
        logger.error("MEMORY CLEAR ERROR  worker=%s  error=%s", worker_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
