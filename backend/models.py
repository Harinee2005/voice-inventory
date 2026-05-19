from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, Date, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime, date as date_type


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


class ConversationMessage(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True)
    role = Column(String(20))
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    action_taken = Column(String(100), nullable=True)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50))
    item_name = Column(String(100))
    details = Column(Text)
    worker = Column(String(50), default="worker")
    session_id = Column(String(100), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
