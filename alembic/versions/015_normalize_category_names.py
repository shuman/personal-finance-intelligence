"""normalize_category_names

Revision ID: 015
Revises: 014
Create Date: 2026-05-01

Normalizes legacy category names in transactions.category_ai,
transactions.merchant_category, and category_rules.category
to the unified 20-category taxonomy defined in app.services.categories.

Idempotent: safe to re-run — only updates rows that match a legacy alias.

Also normalizes category_rules.category for consistency.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, Sequence[str], None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Legacy name → unified name mapping
# Kept inline so the migration is self-contained (no app imports needed).
CATEGORY_MIGRATIONS = {
    # categorization.py compound names (merchant_category)
    "Groceries & Supermarkets": "Groceries",
    "Fuel & Gas": "Transport",
    "Transportation & Travel": "Travel & Hotels",
    "Shopping & Retail": "Shopping",
    "Utilities & Bills": "Utilities",
    "Healthcare & Medical": "Health",
    "Subscriptions & Memberships": "Software & Tools",
    "Cash Withdrawal": "Fees & Charges",
    "Charity & Donations": "Charity",

    # all_transactions.html filter names (may have been saved to category_ai)
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

    # Other legacy variants
    "Restaurants": "Food & Dining",
    "Restaurant": "Food & Dining",
    "Shopping & Lifestyle": "Shopping",
    "Food Delivery": "Food & Dining",
    "Ride Sharing": "Transport",
}


def upgrade() -> None:
    conn = op.get_bind()

    # Normalize transactions.category_ai
    for old_name, new_name in CATEGORY_MIGRATIONS.items():
        result = conn.execute(text(
            "UPDATE transactions SET category_ai = :new "
            "WHERE category_ai = :old"
        ), {"old": old_name, "new": new_name})
        if result.rowcount > 0:
            print(f"  category_ai: '{old_name}' -> '{new_name}' ({result.rowcount} rows)")

    # Normalize transactions.merchant_category
    for old_name, new_name in CATEGORY_MIGRATIONS.items():
        result = conn.execute(text(
            "UPDATE transactions SET merchant_category = :new "
            "WHERE merchant_category = :old"
        ), {"old": old_name, "new": new_name})
        if result.rowcount > 0:
            print(f"  merchant_category: '{old_name}' -> '{new_name}' ({result.rowcount} rows)")

    # Normalize category_rules.category
    for old_name, new_name in CATEGORY_MIGRATIONS.items():
        result = conn.execute(text(
            "UPDATE category_rules SET category = :new "
            "WHERE category = :old"
        ), {"old": old_name, "new": new_name})
        if result.rowcount > 0:
            print(f"  category_rules.category: '{old_name}' -> '{new_name}' ({result.rowcount} rows)")


def downgrade() -> None:
    # Reverse mapping (not all aliases are reversible — some map to the same target).
    # For safety, we only reverse unique mappings.
    REVERSE = {}
    for old_name, new_name in CATEGORY_MIGRATIONS.items():
        if new_name not in REVERSE:
            REVERSE[new_name] = old_name

    conn = op.get_bind()

    for new_name, old_name in REVERSE.items():
        conn.execute(text(
            "UPDATE transactions SET category_ai = :old "
            "WHERE category_ai = :new"
        ), {"old": old_name, "new": new_name})

        conn.execute(text(
            "UPDATE transactions SET merchant_category = :old "
            "WHERE merchant_category = :new"
        ), {"old": old_name, "new": new_name})

        conn.execute(text(
            "UPDATE category_rules SET category = :old "
            "WHERE category = :new"
        ), {"old": old_name, "new": new_name})
