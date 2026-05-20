from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date


class LocationCreate(BaseModel):
    name: str
    description: Optional[str] = None


class LocationResponse(LocationCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class StorageAreaCreate(BaseModel):
    name: str
    description: Optional[str] = None


class StorageAreaResponse(StorageAreaCreate):
    id: int
    location_id: int

    class Config:
        from_attributes = True


class InventoryItemBase(BaseModel):
    item_name: str
    category: str
    quantity: float
    unit: str
    storage_area: str = "General Storage"
    location_name: str = ""
    unit_price: Optional[float] = None
    expiry_date: Optional[str] = None
    updated_by: str = "worker"
    notes: Optional[str] = None
    count_date: Optional[date] = None


class InventoryItemCreate(InventoryItemBase):
    pass


class InventoryItemUpdate(BaseModel):
    item_name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    storage_area: Optional[str] = None
    location_name: Optional[str] = None
    unit_price: Optional[float] = None
    expiry_date: Optional[str] = None
    updated_by: Optional[str] = None
    is_flagged: Optional[bool] = None
    notes: Optional[str] = None


class InventoryItemResponse(InventoryItemBase):
    id: int
    timestamp: datetime
    is_flagged: bool

    class Config:
        from_attributes = True


class VoiceProcessRequest(BaseModel):
    text: str
    session_id: str
    worker_id: str = "worker"
    storage_area: Optional[str] = None
    location_name: Optional[str] = None


class VoiceProcessResponse(BaseModel):
    message: str
    action: str
    data: Dict[str, Any]
    inventory_updated: bool = False
    session_id: str
    audio_base64: Optional[str] = None


class ConversationMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    timestamp: datetime
    action_taken: Optional[str] = None

    class Config:
        from_attributes = True


class ActivityLogResponse(BaseModel):
    id: int
    action: str
    item_name: str
    details: str
    worker: str
    timestamp: datetime

    class Config:
        from_attributes = True


class AnalyticsResponse(BaseModel):
    total_items: int
    total_categories: int
    low_stock_items: List[Dict]
    category_summary: List[Dict]
    expiring_soon: List[Dict]
    recent_activity: List[Dict]
    flagged_items: List[Dict]
    price_analytics: Dict = {}


class UserProfileResponse(BaseModel):
    id: int
    worker_id: str
    emotion_state: str
    tone_preference: str
    personality_notes: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class UserLexiconResponse(BaseModel):
    id: int
    worker_id: str
    original_word: str
    resolved_word: Optional[str] = None
    word_type: str
    usage_count: int
    first_seen: datetime
    last_seen: datetime

    class Config:
        from_attributes = True
