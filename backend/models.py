import os
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, Date, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime, date as date_type, timedelta

_USE_PGVECTOR = os.getenv("DATABASE_URL", "").startswith("postgresql")
if _USE_PGVECTOR:
    from pgvector.sqlalchemy import Vector as _Vector
    def _vec_col(dims: int):
        return Column(_Vector(dims), nullable=True)
else:
    def _vec_col(dims: int):
        return Column(Text, nullable=True)


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    storage_areas = relationship("StorageArea", back_populates="location", cascade="all, delete-orphan")


class StorageArea(Base):
    __tablename__ = "storage_areas"

    id = Column(Integer, primary_key=True, index=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    location = relationship("Location", back_populates="storage_areas")


class InventoryItem(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    item_name = Column(String(100), index=True, nullable=False)
    category = Column(String(50), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(30), nullable=False)
    storage_area = Column(String(100), default="General Storage")
    location_name = Column(String(100), default="")
    expiry_date = Column(String(20), nullable=True)
    updated_by = Column(String(50), default="worker")
    timestamp = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    unit_price = Column(Float, nullable=True)
    is_flagged = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    count_date = Column(Date, default=date_type.today, nullable=False, index=True)
    name_embedding = _vec_col(384)  # fastembed BAAI/bge-small-en-v1.5


class ConversationMessage(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True)
    role = Column(String(20))
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    action_taken = Column(String(100), nullable=True)
    turn_embedding = _vec_col(384)  # fastembed BAAI/bge-small-en-v1.5


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50))
    item_name = Column(String(100))
    details = Column(Text)
    worker = Column(String(50), default="worker")
    session_id = Column(String(100), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(String(100), unique=True, index=True, nullable=False)
    emotion_state = Column(String(50), default="neutral")   # neutral | happy | frustrated | angry
    tone_preference = Column(String(50), default="friendly_fun")  # friendly_fun | formal
    personality_notes = Column(Text, nullable=True)         # cumulative AI observations
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserLexicon(Base):
    __tablename__ = "user_lexicons"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(String(100), index=True, nullable=False)
    original_word = Column(String(100), nullable=False)     # what the user said
    resolved_word = Column(String(100), nullable=True)      # what it actually means
    word_type = Column(String(50), default="unknown")       # unit | item | abbreviation | slang
    usage_count = Column(Integer, default=1)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PendingAction(Base):
    """DB-persisted pending confirmation state — survives app restarts."""
    __tablename__ = "pending_actions"

    session_id = Column(String(100), primary_key=True)
    payload = Column(Text, nullable=False)          # JSON blob of the pending action dict
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)   # auto-cleared after 15 min of no confirm


class RejectedItem(Base):
    """Guard rejections logged per session — prevents ARIA re-suggesting non-food items."""
    __tablename__ = "rejected_items"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True, nullable=False)
    item_name = Column(String(100), nullable=False)
    reason = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class SessionSummary(Base):
    """Compressed episodic memory — summarises old turns to avoid token bloat."""
    __tablename__ = "session_summaries"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True, nullable=False)
    summary = Column(Text, nullable=False)          # 100-word digest of compressed turns
    turn_start = Column(Integer, nullable=False)    # first conversation.id in this batch
    turn_end = Column(Integer, nullable=False)      # last conversation.id in this batch
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkerMemory(Base):
    """Local pgvector replacement for Mem0 — persistent per-worker behavioural memory."""
    __tablename__ = "worker_memories"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(String(100), nullable=False, index=True)
    memory_text = Column(Text, nullable=False)
    memory_type = Column(String(50), default="general")  # tone/lexicon/pattern/personality
    embedding = _vec_col(384)  # fastembed BAAI/bge-small-en-v1.5
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
