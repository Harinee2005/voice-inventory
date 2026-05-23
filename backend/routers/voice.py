import base64
import logging
import time

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from database import get_db
from schemas import VoiceProcessRequest, VoiceProcessResponse
from services.ai_service import process_message
from services.speech_service import transcribe_audio, synthesize_speech

router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


async def _attach_audio(result: dict) -> dict:
    """Synthesize TTS for the response message and embed as base64."""
    try:
        t0 = time.perf_counter()
        audio_bytes = await synthesize_speech(result["message"])
        result["audio_base64"] = base64.b64encode(audio_bytes).decode()
        logger.info("TTS ATTACHED  bytes=%d  elapsed=%.0fms", len(audio_bytes), (time.perf_counter() - t0) * 1000)
    except Exception as e:
        logger.warning("TTS ATTACH FAILED  error=%s", e)
        result["audio_base64"] = None
    return result


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
    )
    result = await _attach_audio(result)
    logger.info(
        "VOICE /process DONE  action=%s  intent=%s  inventory_updated=%s  elapsed=%.0fms",
        result.get("action"), result.get("intent"), result.get("inventory_updated"),
        (time.perf_counter() - t0) * 1000,
    )
    return VoiceProcessResponse(**result)


@router.post("/transcribe")
async def transcribe_audio_endpoint(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    worker_id: str = Form("worker"),
    db: Session = Depends(get_db),
):
    audio_bytes = await audio.read()
    logger.info(
        "VOICE /transcribe  worker=%s  session=%s  filename=%r  bytes=%d",
        worker_id, session_id, audio.filename, len(audio_bytes),
    )
    t0 = time.perf_counter()
    try:
        transcript = await transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        logger.error("VOICE /transcribe FAILED  error=%s", e)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    logger.info("VOICE /transcribe  transcript=%r", transcript[:200])
    result = await process_message(
        text=transcript,
        session_id=session_id,
        worker_id=worker_id,
        db=db,
    )
    result = await _attach_audio(result)
    logger.info(
        "VOICE /transcribe DONE  action=%s  intent=%s  elapsed=%.0fms",
        result.get("action"), result.get("intent"), (time.perf_counter() - t0) * 1000,
    )
    return {"transcript": transcript, **result}


@router.post("/synthesize")
async def synthesize_text(request: dict):
    text = request.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    logger.info("VOICE /synthesize  text_len=%d  text=%r", len(text), text[:100])
    try:
        audio_bytes = await synthesize_speech(text)
        logger.info("VOICE /synthesize DONE  bytes=%d", len(audio_bytes))
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        logger.error("VOICE /synthesize FAILED  error=%s", e)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")
