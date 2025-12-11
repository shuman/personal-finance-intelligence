"""
Merchant categorization utilities.
Pattern-based automatic categorization of transactions.
"""
import re
from typing import Optional, Dict, List


# Merchant category mapping
# Maps merchant name patterns to standardized categories
MERCHANT_CATEGORIES: Dict[str, List[str]] = {
    "Food & Dining": [
        "swiggy", "zomato", "uber eats", "ubereats", "food", "restaurant",
        "pizza", "burger", "cafe", "coffee", "starbucks", "mcdonald",
        "domino", "kfc", "subway", "dunkin", "bakery", "bistro", "diner",
        "eatery", "grill", "kitchen", "bar", "pub", "dining"
    ],
    "Groceries & Supermarkets": [
        "big bazaar", "bigbazaar", "reliance fresh", "dmart", "d-mart",
        "more", "spencers", "grocery", "supermarket", "super market",
        "walmart", "costco", "whole foods", "safeway", "trader joe"
    ],
    "Fuel & Gas": [
        "petrol", "diesel", "fuel", "gas", "bharat petroleum", "bpcl",
        "indian oil", "ioc", "hp", "shell", "essar"
    ],
    "Transportation & Travel": [
        "uber", "ola", "rapido", "lyft", "taxi", "cab", "metro",
        "railway", "irctc", "airline", "flight", "indigo", "spicejet",
        "air india", "vistara", "goair", "makemytrip", "cleartrip",
        "goibibo", "yatra", "booking", "agoda", "hotel", "oyo",
        "treebo", "fabhotel", "parking", "toll"
    ],
    "Shopping & Retail": [
        "amazon", "flipkart", "myntra", "ajio", "nykaa", "shopping",
        "retail", "store", "mall", "shop", "market", "lifestyle",
        "westside", "pantaloons", "max fashion", "h&m", "zara",
        "decathlon", "nike", "adidas", "puma"
    ],
    "Entertainment": [
        "netflix", "amazon prime", "hotstar", "disney", "sony liv",
        "zee5", "voot", "bookmyshow", "pvr", "inox", "cinema",
        "movie", "theatre", "spotify", "apple music", "youtube",
        "gaming", "play station", "xbox", "steam"
    ],
    "Utilities & Bills": [
        "electricity", "water", "gas", "telephone", "mobile", "internet",
        "broadband", "wifi", "airtel", "jio", "vodafone", "vi", "bsnl",
        "bill payment", "recharge", "postpaid", "prepaid"
    ],
    "Healthcare & Medical": [
        "pharmacy", "medical", "hospital", "clinic", "doctor", "health",
        "apollo", "fortis", "max healthcare", "medanta", "medicine",
        "diagnostic", "lab", "pathology", "dental", "wellness"
    ],
    "Education": [
        "school", "college", "university", "education", "tuition",
        "coaching", "course", "training", "udemy", "coursera",
        "skillshare", "books", "bookstore", "amazon kindle"
    ],
    "Insurance": [
        "insurance", "lic", "hdfc life", "icici prudential", "sbi life",
        "bajaj allianz", "max life", "policy", "premium"
    ],
    "Subscriptions & Memberships": [
        "subscription", "membership", "monthly", "annual", "renewal",
        "gym", "fitness", "cult.fit", "gold's gym"
    ],
    "Cash Withdrawal": [
        "atm", "cash withdrawal", "cash advance", "withdrawal"
    ],
    "Financial Services": [
        "bank", "transfer", "payment", "emi", "loan", "credit card",
        "mutual fund", "investment", "zerodha", "groww", "paytm money"
    ],
    "Government & Tax": [
        "income tax", "gst", "government", "municipal", "traffic fine",
        "challan", "fee"
    ],
    "Home & Garden": [
        "furniture", "ikea", "pepperfry", "urban ladder", "home decor",
        "electronics", "appliance", "hardware", "paint"
    ],
    "Personal Care": [
        "salon", "spa", "beauty", "cosmetic", "grooming", "parlor",
        "barber", "hair", "nail"
    ],
    "Charity & Donations": [
        "donation", "charity", "ngo", "trust", "foundation"
    ],
}


def clean_merchant_name(description: str) -> str:
    """
    Clean and normalize merchant name from transaction description.

    Args:
        description: Raw transaction description

    Returns:
        Cleaned merchant name

    Example:
        "SWIGGY *FOOD ORDER  BANGALORE   KA" -> "Swiggy"
        "AMAZON INDIA*1AB2C3" -> "Amazon India"
    """
    if not description:
        return ""

    # Remove extra whitespace
    cleaned = " ".join(description.split())

    # Remove common prefixes/suffixes
    patterns_to_remove = [
        r'\*.*$',  # Remove everything after *
        r'[A-Z]{2}$',  # Remove state code at end
        r'\d{2,}$',  # Remove trailing numbers
        r'^\d+\s+',  # Remove leading numbers
    ]

    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned).strip()

    # Title case
    cleaned = cleaned.title()

    return cleaned


def categorize_transaction(
    description: str,
    merchant_name: Optional[str] = None,
    use_ml: bool = True
) -> str:
    """
    Automatically categorize a transaction using ML + rule-based fallback.

    This function tries ML prediction first (if trained), then falls back
    to rule-based pattern matching if ML confidence is low or model not available.

    Args:
        description: Transaction description
        merchant_name: Optional pre-extracted merchant name
        use_ml: Whether to use ML prediction (default: True)

    Returns:
        Category name or "Other" if no match found

    Example:
        categorize_transaction("SWIGGY *FOOD ORDER") -> "Food & Dining"
        categorize_transaction("AMAZON INDIA") -> "Shopping & Retail"
    """
    # Try ML prediction first if enabled
    if use_ml:
        try:
            from app.ml.categorizer import get_categorizer

            categorizer = get_categorizer()
            ml_category, confidence = categorizer.predict_category(
                description,
                merchant_name,
                min_confidence=0.4  # 40% confidence threshold
            )

            if ml_category:
                # ML prediction is confident enough
                return ml_category
        except Exception as e:
            # ML failed, fall back to rules
            print(f"ML prediction failed, using rules: {e}")

    # Fall back to rule-based categorization
    # Use merchant name if provided, otherwise use description
    text_to_check = (merchant_name or description).lower()

    # Check against category patterns
    for category, patterns in MERCHANT_CATEGORIES.items():
        for pattern in patterns:
            if pattern.lower() in text_to_check:
                return category

    # Default category
    return "Other"


def extract_merchant_info(description: str) -> Dict[str, Optional[str]]:
    """
    Extract merchant information from transaction description.

    Args:
        description: Raw transaction description

    Returns:
        Dictionary with merchant_name, city, state, country

    Example:
        Input: "SWIGGY *FOOD ORDER  BANGALORE   KA"
        Output: {
            "merchant_name": "Swiggy",
            "city": "Bangalore",
            "state": "KA",
            "country": "IN"
        }
    """
    result = {
        "merchant_name": None,
        "city": None,
        "state": None,
        "country": "IN"
    }

    if not description:
        return result

    # Clean the description
    cleaned = clean_merchant_name(description)
    result["merchant_name"] = cleaned

    # Extract city (usually between merchant and state code)
    # Pattern: MERCHANT  CITY  STATE
    parts = description.split()

    # Look for Indian state codes (2 letters at end)
    state_codes = [
        "KA", "MH", "DL", "TN", "UP", "WB", "GJ", "RJ", "KL", "AP",
        "TS", "HR", "PB", "JH", "OR", "CT", "AS", "BR", "HP", "UK"
    ]

    for i, part in enumerate(parts):
        if part.upper() in state_codes:
            result["state"] = part.upper()
            # City is likely the part before state
            if i > 0:
                result["city"] = parts[i-1].title()
            break

    # Check if international transaction
    if any(keyword in description.lower() for keyword in ["usd", "gbp", "eur", "foreign"]):
        result["country"] = None  # Unknown foreign country

    return result


def is_recurring_transaction(description: str) -> bool:
    """
    Detect if a transaction is likely recurring (subscription).

    Args:
        description: Transaction description

    Returns:
        True if likely recurring, False otherwise
    """
    recurring_keywords = [
        "subscription", "monthly", "annual", "recurring", "auto pay",
        "autopay", "netflix", "amazon prime", "spotify", "gym",
        "insurance", "premium", "emi"
    ]

    description_lower = description.lower()
    return any(keyword in description_lower for keyword in recurring_keywords)


def calculate_category_summary(transactions: List[Dict]) -> Dict[str, Dict]:
    """
    Calculate category-wise spending summary from transactions.

    Args:
        transactions: List of transaction dictionaries

    Returns:
        Dictionary mapping category to summary metrics

    Example:
        {
            "Food & Dining": {
                "count": 10,
                "total": 5000.00,
                "avg": 500.00
            },
            ...
        }
    """
    category_data = {}

    for txn in transactions:
        category = txn.get("merchant_category", "Other")
        amount = float(txn.get("amount", 0))

        if category not in category_data:
            category_data[category] = {
                "count": 0,
                "total": 0.0,
                "transactions": []
            }

        category_data[category]["count"] += 1
        category_data[category]["total"] += amount
        category_data[category]["transactions"].append(txn)

    # Calculate averages
    for category in category_data:
        count = category_data[category]["count"]
        total = category_data[category]["total"]
        category_data[category]["avg"] = total / count if count > 0 else 0.0

    return category_data


# Standard transaction types
TRANSACTION_TYPES = {
    "PURCHASE": ["purchase", "sale", "pos", "swipe"],
    "PAYMENT": ["payment", "credit", "received"],
    "REFUND": ["refund", "reversal", "return"],
    "FEE": ["fee", "charge", "annual fee", "late fee"],
    "INTEREST": ["interest", "finance charge"],
    "CASH_ADVANCE": ["cash advance", "atm", "withdrawal"],
    "BALANCE_TRANSFER": ["balance transfer"],
    "ADJUSTMENT": ["adjustment", "credit adjustment"],
}


def detect_transaction_type(description: str, amount: float) -> str:
    """
    Detect transaction type from description.

    Args:
        description: Transaction description
        amount: Transaction amount (negative for debits)

    Returns:
        Transaction type
    """
    description_lower = description.lower()

    # Check patterns
    for txn_type, keywords in TRANSACTION_TYPES.items():
        for keyword in keywords:
            if keyword in description_lower:
                return txn_type

    # Default based on amount
    if amount < 0:
        return "PURCHASE"
    else:
        return "PAYMENT"
