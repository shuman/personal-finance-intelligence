"""
Unified category taxonomy for all money events across the platform.

This is the single source of truth for category names. Every service, template,
and migration should reference UNIFIED_CATEGORIES instead of maintaining local lists.
"""

# The canonical 20-category taxonomy.
# Used by: CategoryEngine (statement transactions), DailyExpenseService (cash),
#          all transaction filters, reports, and signals.
UNIFIED_CATEGORIES = [
    "Groceries",
    "Food & Dining",
    "Transport",
    "Health",
    "Utilities",
    "Shopping",
    "Software & Tools",
    "Freelancing",
    "Entertainment",
    "Fees & Charges",
    "Financial Services",
    "Travel & Hotels",
    "Education",
    "Insurance",
    "Charity",
    "Government & Tax",
    "Personal Care",
    "Home & Garden",
    "Bills & EMI",
    "Other",
]

# Mapping from legacy category names (found in existing data) to unified names.
# Used by data migration to normalize old Transaction rows.
CATEGORY_ALIASES: dict[str, str] = {
    # all_transactions.html filter names
    "Dining": "Food & Dining",
    "Transportation": "Transport",
    "Healthcare": "Health",
    "Travel": "Travel & Hotels",
    "Subscriptions": "Software & Tools",
    "Gas": "Transport",
    "Salon": "Personal Care",
    "Salon & Beauty": "Personal Care",
    "Electronics": "Shopping",
    "Clothing": "Shopping",
    "Home": "Home & Garden",

    # categorization.py MERCHANT_CATEGORIES compound names
    "Groceries & Supermarkets": "Groceries",
    "Fuel & Gas": "Transport",
    "Transportation & Travel": "Travel & Hotels",
    "Shopping & Retail": "Shopping",
    "Utilities & Bills": "Utilities",
    "Healthcare & Medical": "Health",
    "Subscriptions & Memberships": "Software & Tools",
    "Cash Withdrawal": "Fees & Charges",
    "Charity & Donations": "Charity",

    # Any other legacy variants that may exist in data
    "Restaurants": "Food & Dining",
    "Restaurant": "Food & Dining",
    "Shopping & Lifestyle": "Shopping",
    "Food Delivery": "Food & Dining",
    "Ride Sharing": "Transport",
}


def normalize_category(raw: str) -> str:
    """
    Normalize any category string to a UNIFIED_CATEGORIES entry.

    - If it's already a unified category, return as-is.
    - If it matches an alias, return the mapped unified name.
    - Otherwise return 'Other'.
    """
    if raw in UNIFIED_CATEGORIES:
        return raw
    return CATEGORY_ALIASES.get(raw, "Other")
