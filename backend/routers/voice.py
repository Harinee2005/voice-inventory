from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database import get_db
from schemas import VoiceProcessRequest, VoiceProcessResponse
from services.ai_service import process_message
from services.speech_service import transcribe_audio, synthesize_speech

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/process", response_model=VoiceProcessResponse)
async def process_voice_text(request: VoiceProcessRequest, db: Session = Depends(get_db)):
    result = await process_message(
        text=request.text,
        session_id=request.session_id,
        worker_id=request.worker_id,
        storage_area=request.storage_area,
        location_name=request.location_name,
        db=db,
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
    try:
        transcript = await transcribe_audio(audio_bytes, audio.filename or "audio.webm")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    result = await process_message(
        text=transcript,
        session_id=session_id,
        worker_id=worker_id,
        db=db,
    )
    return {"transcript": transcript, **result}


@router.post("/synthesize")
async def synthesize_text(request: dict):
    text = request.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        audio_bytes = await synthesize_speech(text)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")
