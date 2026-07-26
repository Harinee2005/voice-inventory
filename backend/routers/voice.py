import json
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from schemas import VoiceProcessRequest, VoiceProcessResponse
from services.ai_service import process_message, process_message_stream

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


@router.post("/process", response_model=VoiceProcessResponse)
async def process_voice_text(request: VoiceProcessRequest, db: Session = Depends(get_db)):
    logger.info(
        "VOICE /process  worker=%s  session=%s  text=%r  area=%r  location=%r",
        request.worker_id, request.session_id, request.text[:120],
        request.storage_area, request.location_name,
    )
    t0 = time.perf_counter()
    result = await process_message(
        text=request.text,
        session_id=request.session_id,
        worker_id=request.worker_id,
        storage_area=request.storage_area,
        location_name=request.location_name,
        db=db,
        messages=request.messages,
    )
    logger.info(
        "VOICE /process DONE  action=%s  intent=%s  inventory_updated=%s  elapsed=%.0fms",
        result.get("action"), result.get("intent"), result.get("inventory_updated"),
        (time.perf_counter() - t0) * 1000,
    )
    return VoiceProcessResponse(**result)


@router.post("/stream")
async def stream_voice_text(request: VoiceProcessRequest):
    """SSE endpoint — emits status/chunk/done events as the graph executes.

    DB session is owned by the generator (not FastAPI's DI) so it stays
    alive for the full duration of the stream, not just the route handler.
    """
    logger.info(
        "VOICE /stream  worker=%s  session=%s  text=%r",
        request.worker_id, request.session_id, request.text[:120],
    )

    async def event_generator():
        from database import SessionLocal
        db = SessionLocal()
        try:
            async for evt in process_message_stream(
                text=request.text,
                session_id=request.session_id,
                worker_id=request.worker_id,
                storage_area=request.storage_area,
                location_name=request.location_name,
                db=db,
                messages=request.messages,
            ):
                event_type = evt.pop("type", "status")
                yield f"event: {event_type}\ndata: {json.dumps(evt)}\n\n"
        finally:
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
