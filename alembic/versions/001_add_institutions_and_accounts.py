"""Add financial_institutions and accounts tables

Revision ID: 001
Revises:
Create Date: 2026-04-14

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON

revision = "001"
down_revision = "000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_institutions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(20), nullable=False),
        sa.Column("country", sa.String(2), nullable=False, server_default="BD"),
        sa.Column("swift_code", sa.String(20), nullable=True),
        sa.Column("statement_format_hint", sa.String(50), nullable=False, server_default="generic"),
        sa.Column("detection_keywords", JSON(), nullable=True),
        sa.Column("has_sidebar", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sidebar_crop_right_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_structure", sa.String(20), nullable=False, server_default="chronological"),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("account_type", sa.String(20), nullable=False, server_default="credit_card"),
        sa.Column("account_number_masked", sa.String(30), nullable=False),
        sa.Column("account_number_hash", sa.String(64), nullable=True),
        sa.Column("cardholder_name", sa.String(200), nullable=True),
        sa.Column("account_nickname", sa.String(100), nullable=True),
        sa.Column("card_network", sa.String(20), nullable=True),
        sa.Column("card_tier", sa.String(20), nullable=True),
        sa.Column("parent_account_id", sa.Integer(), nullable=True),
        sa.Column("billing_currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column("credit_limit", sa.Numeric(15, 2), nullable=True),
        sa.Column("cash_limit", sa.Numeric(15, 2), nullable=True),
        sa.Column("reward_program_name", sa.String(100), nullable=True),
        sa.Column("reward_type", sa.String(20), nullable=True),
        sa.Column("reward_expiry_months", sa.Integer(), nullable=True),
        sa.Column("points_value_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("color_hex", sa.String(7), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["financial_institutions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_number_hash"),
    )
    op.create_index("ix_accounts_institution_id", "accounts", ["institution_id"])
    op.create_index("ix_accounts_account_number_hash", "accounts", ["account_number_hash"])
    op.create_index("ix_accounts_parent_account_id", "accounts", ["parent_account_id"])


def downgrade() -> None:
    op.drop_table("accounts")
    op.drop_table("financial_institutions")
