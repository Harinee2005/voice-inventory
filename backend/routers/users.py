import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import UserProfile, UserLexicon
from schemas import UserProfileResponse, UserLexiconResponse

router = APIRouter(prefix="/api/users", tags=["users"])
logger = logging.getLogger(__name__)


@router.get("/{worker_id}/profile", response_model=UserProfileResponse)
def get_user_profile(worker_id: str, db: Session = Depends(get_db)):
    logger.info("USERS GET profile  worker=%s", worker_id)
    profile = db.query(UserProfile).filter(UserProfile.worker_id == worker_id).first()
    if not profile:
        logger.warning("USERS GET profile  worker=%s  NOT FOUND", worker_id)
        raise HTTPException(status_code=404, detail="No profile found for this worker yet")
    logger.info(
        "USERS GET profile  worker=%s  emotion=%s  tone=%s  notes=%r",
        worker_id, profile.emotion_state, profile.tone_preference,
        (profile.personality_notes or "")[:80],
    )
    return profile


@router.get("/{worker_id}/lexicons", response_model=List[UserLexiconResponse])
def get_user_lexicons(worker_id: str, db: Session = Depends(get_db)):
    logger.info("USERS GET lexicons  worker=%s", worker_id)
    lexicons = (
        db.query(UserLexicon)
        .filter(UserLexicon.worker_id == worker_id)
        .order_by(UserLexicon.usage_count.desc())
        .all()
    )
    logger.info("USERS GET lexicons  worker=%s  count=%d", worker_id, len(lexicons))
    for lex in lexicons[:10]:
        logger.debug("  LEXICON  %r → %r  type=%s  count=%d", lex.original_word, lex.resolved_word, lex.word_type, lex.usage_count)
    return lexicons


@router.delete("/{worker_id}/lexicons/{lexicon_id}")
def delete_lexicon(worker_id: str, lexicon_id: int, db: Session = Depends(get_db)):
    logger.info("USERS DELETE lexicon  worker=%s  lexicon_id=%d", worker_id, lexicon_id)
    lex = db.query(UserLexicon).filter(
        UserLexicon.id == lexicon_id,
        UserLexicon.worker_id == worker_id,
    ).first()
    if not lex:
        logger.warning("USERS DELETE lexicon  worker=%s  id=%d  NOT FOUND", worker_id, lexicon_id)
        raise HTTPException(status_code=404, detail="Lexicon entry not found")
    logger.info("USERS DELETED lexicon  worker=%s  word=%r", worker_id, lex.original_word)
    db.delete(lex)
    db.commit()
    return {"message": "Lexicon entry deleted"}


@router.get("/", response_model=List[UserProfileResponse])
def list_all_profiles(db: Session = Depends(get_db)):
    profiles = db.query(UserProfile).order_by(UserProfile.worker_id).all()
    logger.info("USERS LIST profiles  count=%d", len(profiles))
    return profiles
