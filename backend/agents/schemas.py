"""
Pydantic schemas for all ARIA agents.

All enum fields use Literal[...] — with strict JSON Schema mode
(completions.parse()) the LLM physically cannot produce a value outside
the allowed set, eliminating invented intent names and category values.

Field validators catch hallucinated quantities, bad dates, and item names
that look like numbers.  Model validators enforce cross-field consistency
(e.g. action=update requires confirmed=True).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ─── Extraction ───────────────────────────────────────────────────────────────

_EXTRACTION_CATEGORY_LITERAL = Literal[
    "vegetable", "fruit", "meat", "seafood", "dairy", "grain",
    "beverage", "frozen", "cleaning", "packaging", "spices",
    "oil", "bakery", "miscellaneous", "UNKNOWN",
]
_EXTRACTION_UNIT_LITERAL = Literal[
    "kg", "g", "litre", "ml", "packet", "box", "bag", "piece",
    "tray", "carton", "tin", "bottle", "can", "dozen", "unit", "UNKNOWN",
]
_CONFIDENCE_LITERAL = Literal["HIGH", "MEDIUM", "LOW"]
_OVERALL_CONFIDENCE_LITERAL = Literal["HIGH", "MEDIUM", "LOW"]


class ExtractionSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = "voice_or_text"
    requires_human_confirmation: bool = True
    overall_confidence: _OVERALL_CONFIDENCE_LITERAL = "LOW"


class ExtractionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = ""
    canonical_name: str = ""
    matched_catalog_item: str = "UNKNOWN"
    category: _EXTRACTION_CATEGORY_LITERAL = "UNKNOWN"
    quantity: Optional[float] = None
    unit: _EXTRACTION_UNIT_LITERAL = "UNKNOWN"
    confidence: _CONFIDENCE_LITERAL = "LOW"
    requires_confirmation: bool = True
    validation_errors: List[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_session: ExtractionSession = Field(default_factory=ExtractionSession)
    items: List[ExtractionItem] = Field(default_factory=list)


# ─── Guard ────────────────────────────────────────────────────────────────────

class GuardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    is_valid: bool
    is_ambiguous: bool
    concern: str = ""
    food_interpretation: str = ""


class GuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_items: bool = False
    all_valid: bool = True
    items: List[GuardItem] = Field(default_factory=list)
    guard_message: str = ""


# ─── ARIA items / data ────────────────────────────────────────────────────────

_CATEGORY_LITERAL = Literal[
    "Vegetables", "Meat", "Seafood", "Dairy", "Dry Goods",
    "Beverages", "Bakery", "Frozen", "Produce", "Cleaning Supplies",
]
_OPERATION_LITERAL = Literal["add", "subtract", "set"]


class ARIAItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_name: str
    category: _CATEGORY_LITERAL = "Dry Goods"
    quantity: Optional[float] = None
    unit: Optional[str] = None
    storage_area: Optional[str] = None
    unit_price: Optional[float] = None
    expiry_date: Optional[str] = None
    operation: _OPERATION_LITERAL = "set"

    @field_validator("quantity")
    @classmethod
    def quantity_non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError(f"quantity cannot be negative: {v}")
        return v

    @field_validator("unit_price")
    @classmethod
    def price_non_zero(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v == 0.0:
            raise ValueError("unit_price of 0 is not valid for a real item")
        return v

    @field_validator("expiry_date")
    @classmethod
    def expiry_date_valid(cls, v: Optional[str]) -> Optional[str]:
        """Null out dates that are malformed, in the past, or implausibly far."""
        if v is None:
            return v
        try:
            parsed = datetime.strptime(v, "%Y-%m-%d").date()
            delta = (parsed - date.today()).days
            if delta < -1 or delta > 1825:   # past or >5 years future
                return None
        except ValueError:
            return None  # malformed format — null out rather than crash
        return v

    @field_validator("item_name")
    @classmethod
    def item_name_not_a_quantity(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("item_name too short")
        # Hallucination guard: model sometimes puts the quantity in item_name
        if re.match(r"^\d+(\.\d+)?\s*(kg|g|ml|l|lbs?|oz|pieces?|units?)?$", v, re.I):
            raise ValueError(f"item_name looks like a quantity: '{v}'")
        return v


class ARIAData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[ARIAItem] = Field(default_factory=list)
    confirmed: bool = False
    flags: List[str] = Field(default_factory=list)


class LexiconEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_word: str
    resolved_word: Optional[str] = None
    word_type: Literal["unit", "item", "abbreviation", "slang"] = "item"


# ─── ARIA result ──────────────────────────────────────────────────────────────

_ACTION_LITERAL = Literal["none", "confirm", "update", "clarify", "flag", "query_result"]
_INTENT_LITERAL = Literal["add", "remove", "set", "query", "expiry", "analytics", "confirm", "deny", "unknown"]
_EMOTION_LITERAL = Literal["neutral", "happy", "frustrated", "angry"]


class ARIAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    action: _ACTION_LITERAL = "none"
    intent: _INTENT_LITERAL = "unknown"
    data: ARIAData = Field(default_factory=ARIAData)
    user_emotion: _EMOTION_LITERAL = "neutral"
    new_lexicons: List[LexiconEntry] = Field(default_factory=list)
    personality_note: Optional[str] = None

    @model_validator(mode="after")
    def action_requires_confirmation(self) -> "ARIAResult":
        """Never commit an update unless confirmed=True — downgrade to confirm."""
        if self.action == "update" and not self.data.confirmed:
            self.action = "confirm"
        if self.action == "update" and self.intent == "unknown":
            self.action = "confirm"
        return self

    @model_validator(mode="after")
    def mutation_requires_items(self) -> "ARIAResult":
        """confirm/update must have at least one item with a parsed quantity."""
        if self.action in ("confirm", "update"):
            has_item_with_qty = any(
                i.item_name and i.quantity is not None
                for i in self.data.items
            )
            if not has_item_with_qty and self.intent not in (
                "deny", "query", "analytics", "confirm"
            ):
                self.action = "clarify"
        return self


# ─── Intent classifier ────────────────────────────────────────────────────────

class IntentResult(BaseModel):
    """
    Output schema for the dedicated intent-classification agent.

    `reasoning` forces chain-of-thought before the final intent label,
    which measurably improves accuracy on short/ambiguous voice inputs.
    """
    model_config = ConfigDict(extra="forbid")

    intent: _INTENT_LITERAL
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                          # CoT inside the JSON
    slot_item: Optional[str] = None         # item name if extractable
    slot_quantity: Optional[float] = None   # quantity if extractable
    slot_unit: Optional[str] = None         # unit if extractable
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
