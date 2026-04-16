"""Create base tables: statements, transactions, fees, interest_charges,
rewards_summary, category_summary, payments

Revision ID: 000
Revises:
Create Date: 2026-04-14

These tables are the foundational schema that all later migrations depend on.
Migration 002 references statements.id in a foreign key, and migration 004
alters statements, transactions, rewards_summary, and category_summary — so
this migration must run first.
"""
from alembic import op
import sqlalchemy as sa

revision = "000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # statements
    # -----------------------------------------------------------------------
    op.create_table(
        "statements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(500), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("upload_date", sa.DateTime(), nullable=True),
        sa.Column("statement_period_from", sa.Date(), nullable=True),
        sa.Column("statement_period_to", sa.Date(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "extraction_status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_statements_file_hash", "statements", ["file_hash"])

    # -----------------------------------------------------------------------
    # transactions
    # -----------------------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("merchant", sa.String(200), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("description", sa.String(500), nullable=True),
        # Legacy FX columns — referenced by migration 004 backfill
        sa.Column("foreign_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("foreign_currency", sa.String(3), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transactions_statement_id", "transactions", ["statement_id"]
    )
    op.create_index(
        "ix_transactions_transaction_date", "transactions", ["transaction_date"]
    )

    # -----------------------------------------------------------------------
    # fees
    # -----------------------------------------------------------------------
    op.create_table(
        "fees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("fee_type", sa.String(100), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("fee_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fees_statement_id", "fees", ["statement_id"])

    # -----------------------------------------------------------------------
    # interest_charges
    # -----------------------------------------------------------------------
    op.create_table(
        "interest_charges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("interest_type", sa.String(50), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("charge_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interest_charges_statement_id", "interest_charges", ["statement_id"]
    )

    # -----------------------------------------------------------------------
    # rewards_summary
    # -----------------------------------------------------------------------
    op.create_table(
        "rewards_summary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("reward_type", sa.String(50), nullable=True),
        sa.Column("points_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_redeemed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rewards_summary_statement_id", "rewards_summary", ["statement_id"]
    )

    # -----------------------------------------------------------------------
    # category_summary
    # -----------------------------------------------------------------------
    op.create_table(
        "category_summary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_category_summary_statement_id", "category_summary", ["statement_id"]
    )

    # -----------------------------------------------------------------------
    # payments
    # -----------------------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"], ["statements.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payments_statement_id", "payments", ["statement_id"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("category_summary")
    op.drop_table("rewards_summary")
    op.drop_table("interest_charges")
    op.drop_table("fees")
    op.drop_table("transactions")
    op.drop_table("statements")
