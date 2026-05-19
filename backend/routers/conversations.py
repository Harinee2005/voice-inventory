from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import ConversationMessage, ActivityLog
from schemas import ConversationMessageResponse, ActivityLogResponse

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("/{session_id}", response_model=List[ConversationMessageResponse])
def get_conversation(session_id: str, limit: int = 50, db: Session = Depends(get_db)):
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.timestamp.asc())
        .limit(limit)
        .all()
    )
    return messages


@router.delete("/{session_id}")
def clear_conversation(session_id: str, db: Session = Depends(get_db)):
    db.query(ConversationMessage).filter(
        ConversationMessage.session_id == session_id
    ).delete()
    db.commit()
    return {"message": "Conversation cleared"}


@router.get("/activity/feed", response_model=List[ActivityLogResponse])
def get_activity_feed(limit: int = 30, db: Session = Depends(get_db)):
    logs = (
        db.query(ActivityLog)
        .order_by(ActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return logs
