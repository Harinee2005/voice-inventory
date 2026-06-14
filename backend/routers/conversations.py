import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import ConversationMessage, ActivityLog
from schemas import ConversationMessageResponse, ActivityLogResponse
from services.ai_service import clear_session_pending

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


@router.get("/{session_id}", response_model=List[ConversationMessageResponse])
def get_conversation(session_id: str, limit: int = 50, db: Session = Depends(get_db)):
    logger.info("CONVERSATIONS GET  session=%s  limit=%d", session_id, limit)
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.timestamp.asc())
        .limit(limit)
        .all()
    )
    logger.info("CONVERSATIONS GET  session=%s  returned=%d messages", session_id, len(messages))
    return messages


@router.delete("/{session_id}")
def clear_conversation(session_id: str, db: Session = Depends(get_db)):
    logger.warning("CONVERSATIONS CLEAR  session=%s  (all messages will be deleted)", session_id)
    deleted = db.query(ConversationMessage).filter(
        ConversationMessage.session_id == session_id
    ).delete()
    db.commit()
    clear_session_pending(session_id, db)
    logger.info("CONVERSATIONS CLEARED  session=%s  deleted=%d", session_id, deleted)
    return {"message": "Conversation cleared"}


@router.get("/activity/feed", response_model=List[ActivityLogResponse])
def get_activity_feed(limit: int = 30, db: Session = Depends(get_db)):
    logger.info("ACTIVITY FEED  limit=%d", limit)
    logs = (
        db.query(ActivityLog)
        .order_by(ActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    logger.info("ACTIVITY FEED  returned=%d records", len(logs))
    for log in logs[:5]:
        logger.debug("  ACTIVITY  action=%s  item=%r  worker=%s  ts=%s", log.action, log.item_name, log.worker, log.timestamp)
    return logs
