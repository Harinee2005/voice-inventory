from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class GuardItem(BaseModel):
    name: str
    is_valid: bool
    is_ambiguous: bool
    concern: str = ""
    food_interpretation: str = ""


class GuardResult(BaseModel):
    has_items: bool = False
    all_valid: bool = True
    items: List[GuardItem] = Field(default_factory=list)
    guard_message: str = ""


class ARIAItem(BaseModel):
    item_name: str
    category: str = "Unknown"
    quantity: Optional[float] = None
    unit: Optional[str] = None
    storage_area: Optional[str] = None
    unit_price: Optional[float] = None
    expiry_date: Optional[str] = None
    operation: str = "set"


class ARIAData(BaseModel):
    items: List[ARIAItem] = Field(default_factory=list)
    confirmed: bool = False
    flags: List[str] = Field(default_factory=list)


class LexiconEntry(BaseModel):
    original_word: str
    resolved_word: Optional[str] = None
    word_type: str = "unknown"   # unit | item | abbreviation | slang


class ARIAResult(BaseModel):
    message: str
    action: str = "none"
    intent: str = "unknown"
    data: ARIAData = Field(default_factory=ARIAData)
    user_emotion: str = "neutral"           # neutral | happy | frustrated | angry
    new_lexicons: List[LexiconEntry] = Field(default_factory=list)
    personality_note: Optional[str] = None  # brief observation about this worker's style
