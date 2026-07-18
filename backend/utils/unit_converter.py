FUZZY_UNIT_ALIASES = {
    # Misspellings — ONLY actual typos belong here. Valid spellings
    # (litre/litres, kilo/kilos, carton…) live in UNIT_ALIASES and must
    # never be flagged as fuzzy — workers use them constantly.
    "littles": "liters",
    "litters": "liters",
    "kilogramme": "kg",
    "kilogrammes": "kg",
    "grame": "g",
    "grames": "g",
    "grms": "g",
    "peices": "pieces",
    "pices": "pieces",
    "peaces": "pieces",
    "botttles": "bottles",
    "botles": "bottles",
    "bttles": "bottles",
    "canes": "cans",
    "caans": "cans",
    "boxs": "boxes",
    "bxs": "boxes",
    "pakkets": "packets",
    "packt": "packets",
    "mililiters": "ml",
    "mililit": "ml",
    "mls": "ml",
    "galons": "gallons",
    "galon": "gallons",
    # Voice-recognition mishearings (speech-to-text phonetic errors)
    "letters": "liters",
    "leaders": "liters",
    "leader": "liter",
    "litters": "liters",
    "leaders": "liters",
    "killos": "kg",
    "grems": "g",
    "peces": "pieces",
    "peases": "pieces",
    "bottels": "bottles",
    "pakages": "packages",
}

UNIT_ALIASES = {
    "kgs": "kg", "kilograms": "kg", "kilogram": "kg",
    "lbs": "lb", "pounds": "lb", "pound": "lb",
    "grams": "g", "gram": "g",
    "ounces": "oz", "ounce": "oz",
    "l": "liters", "lit": "liters", "liter": "liters", "litre": "liters", "litres": "liters",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "gallon": "gallons", "gal": "gallons",
    "pcs": "pieces", "piece": "pieces", "pc": "pieces",
    "item": "pieces", "items": "pieces", "unit": "pieces", "units": "pieces",
    "pkt": "packets", "packet": "packets",
    "box": "boxes", "carton": "cartons", "can": "cans",
    "bottle": "bottles", "bag": "bags",
}

CONVERSION_FACTORS = {
    ("kg", "lb"): 2.20462,
    ("lb", "kg"): 0.453592,
    ("kg", "g"): 1000.0,
    ("g", "kg"): 0.001,
    ("lb", "oz"): 16.0,
    ("oz", "lb"): 0.0625,
    ("oz", "g"): 28.3495,
    ("g", "oz"): 0.035274,
    ("liters", "ml"): 1000.0,
    ("ml", "liters"): 0.001,
    ("liters", "gallons"): 0.264172,
    ("gallons", "liters"): 3.78541,
}

UNIT_GROUPS = {
    "weight": {"kg", "lb", "g", "oz"},
    "volume": {"liters", "ml", "gallons"},
    "count": {"pieces"},
    "packaging": {"packets", "boxes", "cartons", "cans", "bottles", "bags"},
}


from typing import Optional


def normalize_unit(unit: str) -> str:
    if not unit:
        return "pieces"
    return UNIT_ALIASES.get(unit.lower().strip(), unit.lower().strip())


def can_convert(from_unit: str, to_unit: str) -> bool:
    f = normalize_unit(from_unit)
    t = normalize_unit(to_unit)
    return f == t or (f, t) in CONVERSION_FACTORS


def convert(quantity: float, from_unit: str, to_unit: str) -> float:
    f = normalize_unit(from_unit)
    t = normalize_unit(to_unit)
    if f == t:
        return quantity
    if (f, t) in CONVERSION_FACTORS:
        return round(quantity * CONVERSION_FACTORS[(f, t)], 4)
    raise ValueError(f"Cannot convert {from_unit} to {to_unit}")


def are_compatible_units(unit1: str, unit2: str) -> bool:
    u1 = normalize_unit(unit1)
    u2 = normalize_unit(unit2)
    if u1 == u2:
        return True
    for group in UNIT_GROUPS.values():
        if u1 in group and u2 in group:
            return True
    return False


def get_unit_group(unit: str) -> str:
    u = normalize_unit(unit)
    for group_name, group_units in UNIT_GROUPS.items():
        if u in group_units:
            return group_name
    return "packaging"


# ── Physical-type classification for deterministic unit auto-correction ──────
# Implements ARIA RULE 1 in code: a solid measured by volume (or a liquid
# measured by weight) is physically impossible and is corrected without asking.
# Packaging/count units (bottles, boxes, cases…) are business choices and are
# NEVER overridden (RULE 10 — trust the worker's units).

_LIQUID_KEYWORDS = {
    "milk", "oil", "juice", "water", "sauce", "vinegar", "stock", "broth",
    "cream", "syrup", "soda", "cola", "coke", "wine", "beer", "smoothie",
    "milkshake", "lemonade", "ketchup", "mayonnaise", "dressing",
}
_COUNTABLE_KEYWORDS = {"egg", "eggs"}

# Extraction-agent categories (lowercase) + ARIA categories (lowercased here)
_SOLID_CATEGORIES = {
    "vegetable", "fruit", "meat", "seafood", "grain", "spices", "bakery",
    "vegetables", "dry goods", "produce",
}
_LIQUID_CATEGORIES = {"oil", "beverage", "beverages"}


def classify_item_physical_type(item_name: str, category: str = "") -> str:
    """Return "solid" | "liquid" | "countable" | "unknown".

    Keyword match on the item name wins over the category (dairy contains both
    liquid milk and solid cheese, so category alone is unreliable). When
    neither keywords nor category give a confident answer, return "unknown" —
    callers must never guess on unknown.
    """
    import re as _re
    words = set(_re.findall(r"[a-z]+", (item_name or "").lower()))
    if words & _COUNTABLE_KEYWORDS:
        return "countable"
    if words & _LIQUID_KEYWORDS:
        return "liquid"
    cat = (category or "").lower()
    if cat in _LIQUID_CATEGORIES:
        return "liquid"
    if cat in _SOLID_CATEGORIES:
        return "solid"
    return "unknown"


def incompatible_unit_correction(
    item_name: str, category: str, unit: str
) -> Optional[str]:
    """Return the corrected unit when the given unit is physically impossible
    for the item, else None.

      solid  + volume unit  → "kg"
      liquid + weight unit  → "litre"

    Quantity is intentionally left untouched (matches ARIA RULE 1 behaviour).
    Packaging/count units and unknown physical types always return None.
    """
    if not unit or unit == "UNKNOWN":
        return None
    group = get_unit_group(unit)
    ptype = classify_item_physical_type(item_name, category)
    if ptype == "solid" and group == "volume":
        return "kg"
    if ptype == "liquid" and group == "weight":
        return "litre"
    return None


def fuzzy_match_unit(word: str) -> Optional[str]:
    """Return corrected unit only for ACTUAL typos in FUZZY_UNIT_ALIASES.
    Valid aliases (kgs, liter, litre, kilo, pcs…) return None — they are
    correct, not fuzzy. UNIT_ALIASES always wins over the fuzzy dict so a
    valid spelling can never be flagged as a typo."""
    w = word.lower().strip()
    if w in UNIT_ALIASES or w in UNIT_ALIASES.values():
        return None
    return FUZZY_UNIT_ALIASES.get(w)


def extract_fuzzy_units(text: str) -> list:
    """
    Scan text for potential unit typos.
    Returns list of (original_word, corrected_unit) tuples.
    Pattern: number followed by unknown word followed by 'of' or item name.
    """
    import re
    matches = []
    # Match patterns like "5 littles of" or "3 kilo chicken"
    pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s+([a-zA-Z]+)\b', re.IGNORECASE)
    known_items = set()  # Don't flag actual known food items
    for match in pattern.finditer(text):
        word = match.group(2).lower()
        correction = fuzzy_match_unit(word)
        if correction and correction != word:
            matches.append((match.group(2), correction))
    return matches
