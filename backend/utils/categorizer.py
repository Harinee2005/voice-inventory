CATEGORY_KEYWORDS = {
    "Dairy": [
        "milk", "cheese", "butter", "yogurt", "cream", "paneer", "curd",
        "ghee", "whey", "cottage", "mozzarella", "cheddar", "parmesan",
    ],
    "Vegetables": [
        "tomato", "onion", "potato", "carrot", "pepper", "lettuce", "cucumber",
        "spinach", "broccoli", "cabbage", "garlic", "ginger", "zucchini",
        "eggplant", "celery", "corn", "pea", "mushroom", "radish", "beetroot",
        "asparagus", "artichoke", "kale", "leek", "scallion",
    ],
    "Meat": [
        "chicken", "beef", "pork", "lamb", "turkey", "mutton", "veal",
        "bacon", "sausage", "ham", "steak", "mince", "duck", "goat",
        "salami", "pepperoni",
    ],
    "Seafood": [
        "fish", "shrimp", "prawn", "salmon", "tuna", "crab", "lobster",
        "squid", "cod", "tilapia", "mackerel", "sardine", "oyster", "clam",
        "scallop", "anchovy",
    ],
    "Frozen": [
        "frozen", "ice cream", "gelato", "sorbet", "popsicle",
    ],
    "Beverages": [
        "cola", "juice", "water", "coffee", "tea", "soda", "drink",
        "lemonade", "smoothie", "beer", "wine", "cocktail", "sprite",
        "pepsi", "coke", "energy drink", "sparkling", "mineral water",
    ],
    "Dry Goods": [
        "flour", "rice", "sugar", "salt", "spice", "pasta", "oil", "vinegar",
        "sauce", "ketchup", "mayonnaise", "mustard", "soy", "honey", "cereal",
        "oats", "lentil", "chickpea", "noodles", "bread crumb", "cornstarch",
        "baking powder", "yeast", "seasoning",
    ],
    "Bakery": [
        "bread", "cake", "pastry", "bun", "roll", "muffin", "croissant",
        "donut", "bagel", "cookie", "biscuit", "cracker", "tortilla", "wrap",
        "pita",
    ],
    "Produce": [
        "apple", "banana", "orange", "grape", "strawberry", "lemon", "lime",
        "mango", "pineapple", "watermelon", "peach", "pear", "cherry",
        "blueberry", "herb", "basil", "parsley", "cilantro", "mint",
        "thyme", "oregano", "rosemary",
    ],
}

CATEGORIES = list(CATEGORY_KEYWORDS.keys())


def categorize_item(item_name: str) -> str:
    item_lower = item_name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in item_lower:
                return category
    return "Unknown"


def get_category_confidence(item_name: str) -> tuple[str, bool]:
    category = categorize_item(item_name)
    return category, category != "Unknown"
