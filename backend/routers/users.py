from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import UserProfile, UserLexicon
from schemas import UserProfileResponse, UserLexiconResponse

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{worker_id}/profile", response_model=UserProfileResponse)
def get_user_profile(worker_id: str, db: Session = Depends(get_db)):
    profile = db.query(UserProfile).filter(UserProfile.worker_id == worker_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No profile found for this worker yet")
    return profile


@router.get("/{worker_id}/lexicons", response_model=List[UserLexiconResponse])
def get_user_lexicons(worker_id: str, db: Session = Depends(get_db)):
    return (
        db.query(UserLexicon)
        .filter(UserLexicon.worker_id == worker_id)
        .order_by(UserLexicon.usage_count.desc())
        .all()
    )


@router.delete("/{worker_id}/lexicons/{lexicon_id}")
def delete_lexicon(worker_id: str, lexicon_id: int, db: Session = Depends(get_db)):
    lex = db.query(UserLexicon).filter(
        UserLexicon.id == lexicon_id,
        UserLexicon.worker_id == worker_id,
    ).first()
    if not lex:
        raise HTTPException(status_code=404, detail="Lexicon entry not found")
    db.delete(lex)
    db.commit()
    return {"message": "Lexicon entry deleted"}


@router.get("/", response_model=List[UserProfileResponse])
def list_all_profiles(db: Session = Depends(get_db)):
    return db.query(UserProfile).order_by(UserProfile.worker_id).all()
