FUZZY_UNIT_ALIASES = {
    # Misspellings
    "littles": "liters",
    "litters": "liters",
    "litre": "liters",
    "litres": "liters",
    "kilo": "kg",
    "kilos": "kg",
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
    "carton": "cartons",
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
    "crates": "crates",  # already correct but include for normalization
}

UNIT_ALIASES = {
    "kgs": "kg", "kilograms": "kg", "kilogram": "kg",
    "lbs": "lb", "pounds": "lb", "pound": "lb",
    "grams": "g", "gram": "g",
    "ounces": "oz", "ounce": "oz",
    "l": "liters", "liter": "liters", "litre": "liters", "litres": "liters",
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


def fuzzy_match_unit(word: str) -> Optional[str]:
    """Return corrected unit only for ACTUAL typos in FUZZY_UNIT_ALIASES.
    Valid aliases (kgs, liter, pcs…) return None — they are correct, not fuzzy."""
    return FUZZY_UNIT_ALIASES.get(word.lower().strip())


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
