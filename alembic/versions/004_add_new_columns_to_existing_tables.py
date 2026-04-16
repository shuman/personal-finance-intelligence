"""Add new columns to existing tables + backfill data

Revision ID: 004
Revises: 003
Create Date: 2026-04-14

Adds:
  statements: extraction_method, ai_confidence, statement_type
  statements: fk + index for account_id (column already exists from migration 000)
  transactions: billing_amount, billing_currency, original_amount,
                original_currency, fx_rate_applied, category_ai, subcategory_ai,
                category_confidence, category_source, category_rule_id
  transactions: fk + index for account_id (column already exists from migration 000)
  rewards_summary: reward_program_name, expired_this_period, accelerated_tiers,
                   estimated_value_bdt
  category_summary: subcategory_name column

Backfills:
  transactions.billing_amount = amount (for existing rows)
  transactions.billing_currency = currency (for existing rows)
  transactions.original_amount = foreign_amount (where not null)
  transactions.original_currency = foreign_currency (where not null)
  transactions.fx_rate_applied = exchange_rate (where not null)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # statements table — new columns
    # -----------------------------------------------------------------------
    with op.batch_alter_table("statements") as batch_op:
        batch_op.add_column(sa.Column("extraction_method", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("ai_confidence", sa.Numeric(3, 2), nullable=True))
        batch_op.add_column(
            sa.Column("statement_type", sa.String(20), nullable=False, server_default="credit")
        )

    with op.batch_alter_table("statements") as batch_op:
        batch_op.create_foreign_key(
            "fk_statements_account_id",
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_statements_account_id", ["account_id"])

    # -----------------------------------------------------------------------
    # transactions table — new columns
    # -----------------------------------------------------------------------
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("billing_amount", sa.Numeric(15, 2), nullable=True))
        batch_op.add_column(sa.Column("billing_currency", sa.String(3), nullable=True))
        batch_op.add_column(sa.Column("original_amount", sa.Numeric(15, 2), nullable=True))
        batch_op.add_column(sa.Column("original_currency", sa.String(3), nullable=True))
        batch_op.add_column(sa.Column("fx_rate_applied", sa.Numeric(10, 6), nullable=True))
        batch_op.add_column(sa.Column("category_ai", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("subcategory_ai", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("category_confidence", sa.Numeric(3, 2), nullable=True))
        batch_op.add_column(sa.Column("category_source", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("category_rule_id", sa.Integer(), nullable=True))

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.create_foreign_key(
            "fk_transactions_account_id",
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_transactions_category_rule_id",
            "category_rules",
            ["category_rule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_transactions_account_id", ["account_id"])
        batch_op.create_index("ix_transactions_category_ai", ["category_ai"])

    # Backfill billing_amount and billing_currency from existing amount/currency columns
    op.execute(
        "UPDATE transactions SET billing_amount = amount WHERE billing_amount IS NULL"
    )
    op.execute(
        "UPDATE transactions SET billing_currency = currency WHERE billing_currency IS NULL"
    )
    # Backfill FX fields from legacy column names
    op.execute(
        "UPDATE transactions SET original_amount = foreign_amount "
        "WHERE original_amount IS NULL AND foreign_amount IS NOT NULL"
    )
    op.execute(
        "UPDATE transactions SET original_currency = foreign_currency "
        "WHERE original_currency IS NULL AND foreign_currency IS NOT NULL"
    )
    op.execute(
        "UPDATE transactions SET fx_rate_applied = exchange_rate "
        "WHERE fx_rate_applied IS NULL AND exchange_rate IS NOT NULL"
    )

    # -----------------------------------------------------------------------
    # rewards_summary table — new columns
    # -----------------------------------------------------------------------
    with op.batch_alter_table("rewards_summary") as batch_op:
        batch_op.add_column(sa.Column("reward_program_name", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column("expired_this_period", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("accelerated_tiers", JSON(), nullable=True))
        batch_op.add_column(sa.Column("estimated_value_bdt", sa.Numeric(15, 2), nullable=True))

    # -----------------------------------------------------------------------
    # category_summary table — new subcategory_name column
    # -----------------------------------------------------------------------
    with op.batch_alter_table("category_summary") as batch_op:
        batch_op.add_column(sa.Column("subcategory_name", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("category_summary") as batch_op:
        batch_op.drop_column("subcategory_name")

    with op.batch_alter_table("rewards_summary") as batch_op:
        batch_op.drop_column("estimated_value_bdt")
        batch_op.drop_column("accelerated_tiers")
        batch_op.drop_column("expired_this_period")
        batch_op.drop_column("reward_program_name")

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_transactions_category_ai")
        batch_op.drop_index("ix_transactions_account_id")
        batch_op.drop_column("category_rule_id")
        batch_op.drop_column("category_source")
        batch_op.drop_column("category_confidence")
        batch_op.drop_column("subcategory_ai")
        batch_op.drop_column("category_ai")
        batch_op.drop_column("fx_rate_applied")
        batch_op.drop_column("original_currency")
        batch_op.drop_column("original_amount")
        batch_op.drop_column("billing_currency")
        batch_op.drop_column("billing_amount")

    with op.batch_alter_table("statements") as batch_op:
        batch_op.drop_index("ix_statements_account_id")
        batch_op.drop_column("statement_type")
        batch_op.drop_column("ai_confidence")
        batch_op.drop_column("extraction_method")
